from __future__ import annotations

import asyncio

import discord

from casino.container import AppContainer
from casino.discord_bot.installs import is_inline_game
from casino.discord_bot.presentation.admin_alerts import AdminChannelPresenter
from casino.discord_bot.presentation.builder import CasinoPanel
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.discord_bot.presentation.notifications import GameNotificationPresenter
from casino.domain.enums import GameStatus
from casino.domain.records import DicePlayResult, GameRecord, is_house_seat
from casino.errors import GameError
from casino.logging.logger import get_logger
from casino.utils.channel_names import GameChannelNamer


class GameTablePresenter:
    """Creates private table channels and mirrors game state to listing, outcomes, and PF."""

    def __init__(
        self,
        bot: discord.Client,
        container: AppContainer,
        notifications: GameNotificationPresenter | None = None,
        admin_alerts: AdminChannelPresenter | None = None,
    ) -> None:
        self._bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)
        self._notifications = notifications
        self._admin_alerts = admin_alerts
        self._namer = GameChannelNamer()
        self._logger = get_logger("casino.discord.table")
        self._dice_locks: dict[str, asyncio.Lock] = {}

    async def provision(self, game: GameRecord, creator: discord.abc.User) -> GameRecord:
        listing_channel = await self._open_games_destination(game)
        if listing_channel is None:
            raise GameError("Ask an admin to set the open coinflip or dice channel in `/admin config`.")
        refreshed = game
        if game.guild_id:
            await self._container.games.attach_guild(game.game_id, game.guild_id)
        if game.guild_id and game.table_number is None:
            number = await self._container.guild_configs.take_next_table_number(game.guild_id)
            await self._container.games.attach_table_number(game.game_id, number)
            game.table_number = number
        private = await self._create_private_channel(game, creator)
        if private is not None:
            overview_panel = self._embeds.table_overview(game)
            overview = await private.send(**overview_panel.send_kwargs())
            overview_panel.stop()
            await self._container.games.attach_private(game.game_id, str(private.id), str(overview.id))
            refreshed = await self._container.games.get(game.game_id)

        listing_panel = self._embeds.open_game(refreshed)
        listing = await listing_channel.send(**listing_panel.send_kwargs())
        listing_panel.stop()
        await self._container.games.attach_listing(game.game_id, str(listing.channel.id), str(listing.id))
        return await self._container.games.get(game.game_id)

    async def provision_inline(self, game: GameRecord, interaction: discord.Interaction) -> GameRecord:
        if game.guild_id:
            await self._container.games.attach_guild(game.game_id, game.guild_id)
        panel = self._embeds.table_overview(game, include_join=True)
        await interaction.edit_original_response(**panel.send_kwargs())
        panel.stop()
        message = await interaction.original_response()
        channel_id = str(interaction.channel_id or message.channel.id)
        await self._container.games.attach_listing(game.game_id, channel_id, str(message.id))
        return await self._container.games.get(game.game_id)

    def _is_inline(self, game: GameRecord) -> bool:
        return is_inline_game(self._container.settings, game)

    async def on_seat_taken(
        self,
        game: GameRecord,
        member: discord.abc.User,
        *,
        interaction: discord.Interaction | None = None,
    ) -> None:
        try:
            await self._grant_private_access(game, member)
            if game.status is GameStatus.PENDING:
                await self._refresh_lobby(game, interaction=interaction)
        except Exception:
            self._logger.exception("seat_taken_discord_failed", gameId=game.game_id)
        if self._notifications is None:
            return
        try:
            await self._notifications.player_joined(game, member)
        except Exception:
            self._logger.exception("join_notify_failed", gameId=game.game_id)

    async def on_lobby_changed(
        self,
        game: GameRecord,
        *,
        interaction: discord.Interaction | None = None,
    ) -> None:
        try:
            if game.status is GameStatus.PENDING:
                await self._refresh_lobby(game, interaction=interaction)
        except Exception:
            self._logger.exception("lobby_refresh_failed", gameId=game.game_id)

    async def on_resolved(
        self,
        game: GameRecord,
        *,
        interaction: discord.Interaction | None = None,
    ) -> None:
        if self._is_inline(game):
            if game.is_dice():
                await self._edit_listing(
                    game,
                    self._embeds.resolved_game(game, rerun=False),
                    interaction=interaction,
                )
            else:
                game = await self._run_flip_countdown(game, interaction=interaction)
            if self._admin_alerts is not None:
                await self._admin_alerts.on_game_resolved(game)
            return
        await self._delete_listing(game)
        if game.is_dice():
            await self._edit_ref(
                game.private_channel_id,
                game.private_message_id,
                self._embeds.table_overview(game),
            )
            await self._post_private(game, self._embeds.resolved_game(game, rerun=True))
        else:
            await self._edit_ref(
                game.private_channel_id,
                game.private_message_id,
                self._embeds.table_overview(game, conceal_result=True),
            )
            game = await self._run_flip_countdown(game)
            await self._edit_ref(
                game.private_channel_id,
                game.private_message_id,
                self._embeds.table_overview(game),
            )
        await self._post_outcomes(game)
        await self._refresh_provably_fair(game)
        if self._admin_alerts is not None:
            await self._admin_alerts.on_game_resolved(game)

    def _dice_lock(self, key: str) -> asyncio.Lock:
        lock = self._dice_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._dice_locks[key] = lock
        return lock

    async def handle_player_roll(self, channel_id: str, player_id: str) -> DicePlayResult:
        async with self._dice_lock(channel_id):
            result = await self._container.games.roll_dice(channel_id, player_id)
            await self._post_dice_play(result)
            if result.game.status is GameStatus.ACTIVE:
                drained = await self._advance_house_turns(result.game)
                if drained is not None:
                    return drained
            elif result.game.status is GameStatus.COMPLETED:
                await self.on_resolved(result.game)
            return result

    async def on_dice_started(self, game: GameRecord) -> None:
        lock_key = game.private_channel_id or game.game_id
        async with self._dice_lock(lock_key):
            await self._delete_listing(game)
            await self._refresh_lobby(game)
            await self._post_dice_history(game)
            if game.status is GameStatus.COMPLETED:
                await self.on_resolved(game)
                return
            await self._advance_house_turns(game)

    async def on_dice_play(self, result: DicePlayResult) -> None:
        await self._post_dice_play(result)

    async def _advance_house_turns(self, game: GameRecord) -> DicePlayResult | None:
        channel_id = game.private_channel_id
        if not channel_id:
            return None
        last: DicePlayResult | None = None
        try:
            while game.status is GameStatus.ACTIVE:
                current = game.current_turn_id()
                if current is None or not is_house_seat(current):
                    break
                await self._wait_before_house_roll()
                last = await self._container.games.play_next_house_turn(channel_id)
                await self._post_dice_play(last)
                game = last.game
        except Exception:
            self._logger.exception("dice_house_turn_failed", gameId=game.game_id)
            return last
        if game.status is GameStatus.COMPLETED:
            await self.on_resolved(game)
        return last

    async def _post_dice_play(self, result: DicePlayResult) -> None:
        try:
            channel = await self._private_channel(result.game)
            if channel is None:
                return
            for event in result.events:
                panel = self._embeds.dice_roll(result.game, event.roll, event=event)
                await channel.send(**panel.send_kwargs())
                panel.stop()
            await self._refresh_lobby(result.game)
        except Exception:
            self._logger.exception("dice_play_discord_failed", gameId=result.game.game_id)

    async def _post_dice_history(self, game: GameRecord) -> None:
        channel = await self._private_channel(game)
        if channel is None:
            return
        for roll in game.dice_rolls:
            if is_house_seat(roll.player_id):
                await self._wait_before_house_roll()
            panel = self._embeds.dice_roll(game, roll)
            try:
                await channel.send(**panel.send_kwargs())
                panel.stop()
            except discord.HTTPException:
                self._logger.exception("dice_roll_post_failed", gameId=game.game_id)
                return

    async def _wait_before_house_roll(self) -> None:
        delay = self._container.settings.house_dice_roll_delay_seconds
        if delay > 0:
            await asyncio.sleep(delay)

    async def _post_private(self, game: GameRecord, panel: CasinoPanel) -> None:
        channel = await self._private_channel(game)
        if channel is None:
            return
        try:
            await channel.send(**panel.send_kwargs())
            panel.stop()
        except discord.HTTPException:
            self._logger.exception("private_result_post_failed", gameId=game.game_id)

    async def on_player_left(
        self,
        game: GameRecord,
        member: discord.abc.User,
        *,
        interaction: discord.Interaction | None = None,
    ) -> None:
        try:
            await self._revoke_private_access(game, member)
            if game.status is GameStatus.PENDING:
                await self._refresh_lobby(game, interaction=interaction)
        except Exception:
            self._logger.exception("player_left_discord_failed", gameId=game.game_id)

    async def on_table_cleared(
        self,
        game: GameRecord,
        *,
        interaction: discord.Interaction | None = None,
    ) -> None:
        if self._is_inline(game):
            deleted = await self._delete_from_interaction(interaction)
            if not deleted:
                await self._delete_listing(game)
            return
        await self._delete_listing(game)
        await self._delete_private_channel(game)

    async def on_cancelled(
        self,
        game: GameRecord,
        *,
        interaction: discord.Interaction | None = None,
    ) -> None:
        if not game.human_player_ids():
            await self.on_table_cleared(game, interaction=interaction)
            return
        if self._is_inline(game):
            await self._edit_listing(game, self._embeds.cancelled_game(game), interaction=interaction)
            return
        await self._delete_listing(game)
        await self._edit_ref(
            game.private_channel_id,
            game.private_message_id,
            self._embeds.cancelled_game(game),
        )

    async def ensure_provably_fair_panel(
        self,
        guild_id: str,
        latest: GameRecord | None = None,
    ) -> discord.Message | None:
        config = await self._container.guild_configs.get(guild_id)
        if not config.provably_fair_channel_id:
            return None
        if latest is None:
            completed = await self._container.games.list_completed(guild_id, limit=1)
            latest = completed[0] if completed else None
        panel = self._embeds.provably_fair_hub(latest)
        existing = await self._fetch_message(config.provably_fair_channel_id, config.provably_fair_message_id)
        if existing is not None:
            try:
                await existing.edit(**panel.edit_kwargs())
                panel.stop()
                return existing
            except discord.HTTPException:
                self._logger.exception("provably_fair_edit_failed", guildId=guild_id)
        posted = await self._send_to(config.provably_fair_channel_id, panel)
        if posted is not None:
            await self._container.guild_configs.set_pf_message(guild_id, str(posted.id))
        return posted

    async def _create_private_channel(
        self,
        game: GameRecord,
        creator: discord.abc.User,
    ) -> discord.TextChannel | None:
        if not game.guild_id:
            return None
        guild = self._bot.get_guild(int(game.guild_id))
        if guild is None:
            return None
        config = await self._container.guild_configs.get(game.guild_id)
        category_id = config.category_id(game.game_type)
        if not category_id:
            self._logger.info("private_channel_skipped", gameId=game.game_id, reason="category_not_configured")
            return None
        category = guild.get_channel(int(category_id))
        if not isinstance(category, discord.CategoryChannel):
            self._logger.warning("private_channel_category_missing", gameId=game.game_id, categoryId=category_id)
            return None
        me = guild.me
        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,
                read_message_history=True,
            ),
        }
        creator_member = await self._member_for(guild, creator)
        if creator_member is not None:
            overwrites[creator_member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )
        if me is not None:
            overwrites[me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                embed_links=True,
                manage_channels=True,
                read_message_history=True,
            )
        name = self._namer.for_game(game)
        try:
            return await guild.create_text_channel(
                name=name,
                category=category,
                overwrites=overwrites,
                reason=f"{game.game_type.value} {game.game_id}",
            )
        except discord.HTTPException:
            try:
                return await guild.create_text_channel(
                    name=f"{name}-{game.game_id[-4:]}",
                    category=category,
                    overwrites=overwrites,
                    reason=f"{game.game_type.value} {game.game_id}",
                )
            except discord.HTTPException:
                self._logger.exception("private_channel_create_failed", gameId=game.game_id)
                return None

    async def _grant_private_access(self, game: GameRecord, member: discord.abc.User) -> None:
        channel = await self._private_channel(game)
        if channel is None:
            return
        target = await self._member_for(channel.guild, member)
        if target is None:
            self._logger.warning("private_access_member_missing", gameId=game.game_id, userId=str(member.id))
            return
        try:
            await channel.set_permissions(
                target,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )
        except discord.HTTPException:
            self._logger.exception("private_access_failed", gameId=game.game_id, userId=str(member.id))

    async def _revoke_private_access(self, game: GameRecord, member: discord.abc.User) -> None:
        channel = await self._private_channel(game)
        if channel is None:
            return
        target = await self._member_for(channel.guild, member)
        if target is None:
            return
        try:
            await channel.set_permissions(target, overwrite=None)
        except discord.HTTPException:
            self._logger.exception("private_access_revoke_failed", gameId=game.game_id, userId=str(member.id))

    async def _delete_private_channel(self, game: GameRecord) -> None:
        channel = await self._private_channel(game)
        if channel is None:
            return
        try:
            await channel.delete(reason=f"{game.game_type.value} {game.game_id} emptied")
        except discord.HTTPException:
            self._logger.exception("private_channel_delete_failed", gameId=game.game_id)

    async def _member_for(self, guild: discord.Guild, user: discord.abc.User) -> discord.Member | None:
        if isinstance(user, discord.Member) and user.guild.id == guild.id:
            return user
        cached = guild.get_member(user.id)
        if cached is not None:
            return cached
        try:
            return await guild.fetch_member(user.id)
        except discord.HTTPException:
            self._logger.exception("member_fetch_failed", guildId=str(guild.id), userId=str(user.id))
            return None

    async def on_rematch_opened(self, game: GameRecord) -> None:
        channel = await self._private_channel(game)
        if channel is None:
            return
        panel = await self._lobby_panel(game)
        try:
            message = await channel.send(**panel.send_kwargs())
            panel.stop()
        except discord.HTTPException:
            self._logger.exception("rematch_post_failed", gameId=game.game_id)
            return
        await self._container.games.attach_private(game.game_id, str(channel.id), str(message.id))

    async def _refresh_lobby(
        self,
        game: GameRecord,
        *,
        interaction: discord.Interaction | None = None,
    ) -> None:
        if self._is_inline(game):
            await self._edit_listing(game, await self._lobby_panel(game), interaction=interaction)
            return
        await self._edit_ref(game.channel_id, game.message_id, self._embeds.open_game(game))
        await self._edit_ref(game.private_channel_id, game.private_message_id, await self._lobby_panel(game))

    async def _lobby_panel(self, game: GameRecord):
        if game.is_awaiting_rematch_accepts() and game.rematch_of_game_id:
            source = await self._container.games.get(game.rematch_of_game_id)
            return self._embeds.rematch_invite(game, source)
        return self._embeds.table_overview(game, include_join=self._is_inline(game))

    async def _run_flip_countdown(
        self,
        game: GameRecord,
        *,
        interaction: discord.Interaction | None = None,
    ) -> GameRecord:
        flips = game.flip_series() or [None]
        if self._is_inline(game):
            return await self._run_inline_flip_countdown(game, interaction, flips)
        channel = await self._private_channel(game)
        if channel is None:
            return await self._settle_coinflip(game)
        settled = game
        try:
            for index, _side in enumerate(flips):
                last = index == len(flips) - 1
                message = await self._play_flip_countdown(channel.send, game, index)
                if message is None:
                    return await self._settle_coinflip(game)
                if last:
                    settled = await self._settle_coinflip(game)
                    result = self._embeds.resolved_game(settled, rerun=True)
                else:
                    result = self._embeds.flip_reveal(game, index)
                await message.edit(**result.edit_kwargs())
                result.stop()
        except discord.HTTPException:
            self._logger.exception("flip_countdown_failed", gameId=game.game_id)
            settled = await self._settle_coinflip(game)
        return settled

    async def _settle_coinflip(self, game: GameRecord) -> GameRecord:
        if game.status is GameStatus.COMPLETED:
            return game
        try:
            return await self._container.games.finalize_coinflip(game.game_id)
        except Exception:
            self._logger.exception("coinflip_settle_failed", gameId=game.game_id)
            return game

    async def _play_flip_countdown(self, send, game: GameRecord, flip_index: int):
        panel = self._embeds.flip_countdown(game, 3, flip_index=flip_index)
        message = await send(**panel.send_kwargs())
        panel.stop()
        for seconds in (2, 1):
            await asyncio.sleep(1)
            next_panel = self._embeds.flip_countdown(game, seconds, flip_index=flip_index)
            await message.edit(**next_panel.edit_kwargs())
            next_panel.stop()
        await asyncio.sleep(1)
        return message

    async def _run_inline_flip_countdown(
        self,
        game: GameRecord,
        interaction: discord.Interaction | None,
        flips: list,
    ) -> GameRecord:
        settled = game
        for index, _side in enumerate(flips):
            for seconds in (3, 2, 1):
                panel = self._embeds.flip_countdown(game, seconds, flip_index=index)
                if not await self._edit_listing(game, panel, interaction=interaction):
                    self._logger.warning("inline_countdown_skipped", gameId=game.game_id)
                    return await self._settle_coinflip(game)
                await asyncio.sleep(1)
            last = index == len(flips) - 1
            if last:
                settled = await self._settle_coinflip(game)
                result = self._embeds.resolved_game(settled, rerun=False)
            else:
                result = self._embeds.flip_reveal(game, index)
            if not await self._edit_listing(game, result, interaction=interaction):
                self._logger.warning("inline_result_skipped", gameId=game.game_id)
                return settled
        return settled

    async def _edit_listing(
        self,
        game: GameRecord,
        panel: CasinoPanel,
        *,
        interaction: discord.Interaction | None = None,
    ) -> bool:
        if await self._edit_from_interaction(interaction, panel):
            return True
        edited = await self._edit_ref(game.channel_id, game.message_id, panel)
        if not edited:
            panel.stop()
        return edited

    async def _edit_from_interaction(
        self,
        interaction: discord.Interaction | None,
        panel: CasinoPanel,
    ) -> bool:
        if interaction is None:
            return False
        kwargs = panel.edit_kwargs()
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(**kwargs)
            else:
                await interaction.response.edit_message(**kwargs)
            panel.stop()
            return True
        except discord.HTTPException as exc:
            self._logger.info(
                "interaction_webhook_edit_failed",
                code=exc.code,
                status=exc.status,
            )
            return False

    async def _delete_from_interaction(self, interaction: discord.Interaction | None) -> bool:
        if interaction is None:
            return False
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
            await interaction.delete_original_response()
            return True
        except discord.HTTPException:
            return False

    async def _delete_listing(self, game: GameRecord) -> None:
        latest = game
        try:
            latest = await self._container.games.get(game.game_id)
        except Exception:
            self._logger.exception("listing_reload_failed", gameId=game.game_id)
        message_id = latest.message_id or game.message_id
        channel_ids: list[str] = []
        for channel_id in (latest.channel_id, game.channel_id):
            if channel_id and channel_id not in channel_ids:
                channel_ids.append(channel_id)
        if latest.guild_id:
            config = await self._container.guild_configs.get(latest.guild_id)
            listing_id = config.listing_channel_id(latest.game_type)
            if listing_id and listing_id not in channel_ids:
                channel_ids.append(listing_id)
        if not message_id or not channel_ids:
            self._logger.warning(
                "listing_delete_skipped",
                gameId=game.game_id,
                channelId=latest.channel_id,
                messageId=message_id,
            )
            return
        for channel_id in channel_ids:
            if await self._delete_message(channel_id, message_id):
                return
        self._logger.warning("listing_delete_missed", gameId=game.game_id, messageId=message_id)

    async def _delete_message(self, channel_id: str, message_id: str) -> bool:
        channel = await self._messageable(channel_id)
        if channel is None or not hasattr(channel, "get_partial_message"):
            return False
        try:
            await channel.get_partial_message(int(message_id)).delete()
            return True
        except discord.NotFound:
            return False
        except discord.HTTPException:
            self._logger.exception("listing_delete_failed", channelId=channel_id, messageId=message_id)
            return False

    async def _post_outcomes(self, game: GameRecord) -> None:
        if not game.guild_id:
            return
        config = await self._container.guild_configs.get(game.guild_id)
        channel_id = config.outcomes_channel_id(game.game_type)
        await self._send_to(channel_id, self._embeds.resolved_game(game))

    async def _refresh_provably_fair(self, game: GameRecord) -> None:
        if game.status is not GameStatus.COMPLETED or not game.guild_id:
            return
        try:
            await self.ensure_provably_fair_panel(game.guild_id, latest=game)
        except Exception:
            self._logger.exception("provably_fair_refresh_failed", gameId=game.game_id)

    async def _open_games_destination(self, game: GameRecord) -> discord.abc.Messageable | None:
        if not game.guild_id:
            return None
        config = await self._container.guild_configs.get(game.guild_id)
        return await self._messageable(config.listing_channel_id(game.game_type))

    async def _edit_ref(
        self,
        channel_id: str | None,
        message_id: str | None,
        panel: CasinoPanel,
    ) -> bool:
        message = await self._fetch_message(channel_id, message_id)
        if message is None:
            return False
        try:
            await message.edit(**panel.edit_kwargs())
            panel.stop()
            return True
        except discord.HTTPException:
            self._logger.exception("game_message_edit_failed", channelId=channel_id, messageId=message_id)
            return False

    async def _send_to(self, channel_id: str | None, panel: CasinoPanel) -> discord.Message | None:
        channel = await self._messageable(channel_id)
        if channel is None:
            return None
        try:
            message = await channel.send(**panel.send_kwargs())
            panel.stop()
            return message
        except discord.HTTPException:
            self._logger.exception("config_channel_post_failed", channelId=channel_id)
            return None

    async def _messageable(self, channel_id: str | None) -> discord.abc.Messageable | None:
        if not channel_id:
            return None
        channel = self._bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self._bot.fetch_channel(int(channel_id))
            except discord.HTTPException:
                return None
        return channel if isinstance(channel, discord.abc.Messageable) else None

    async def _private_channel(self, game: GameRecord) -> discord.TextChannel | None:
        if not game.private_channel_id:
            return None
        channel = self._bot.get_channel(int(game.private_channel_id))
        if channel is None:
            try:
                channel = await self._bot.fetch_channel(int(game.private_channel_id))
            except discord.HTTPException:
                return None
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _fetch_message(self, channel_id: str | None, message_id: str | None) -> discord.Message | None:
        if not channel_id or not message_id:
            return None
        channel = self._bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self._bot.fetch_channel(int(channel_id))
            except discord.HTTPException:
                return None
        if not isinstance(channel, discord.abc.Messageable):
            return None
        try:
            return await channel.fetch_message(int(message_id))
        except discord.HTTPException:
            return None
