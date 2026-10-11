from __future__ import annotations

import discord
from discord.ext import commands

from casino.container import AppContainer
from casino.discord_bot.presentation.builder import CasinoPanel
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.discord_bot.presentation.errors import DiscordErrorPresenter
from casino.discord_bot.presentation.ids import ComponentIdCodec
from casino.discord_bot.presentation.modals import (
    ProvablyFairCheckModal,
    VerifyCodeModal,
    WithdrawModal,
)
from casino.domain.enums import CoinSide, GameStatus
from casino.errors import AppError
from casino.logging.logger import LogContext, get_logger


class ComponentRouterCog(commands.Cog):
    """Persistent component handler so buttons survive process restarts."""

    def __init__(self, bot: commands.Bot, container: AppContainer) -> None:
        self.bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)
        self._ids = ComponentIdCodec()
        self._errors = DiscordErrorPresenter()
        self._logger = get_logger("casino.discord.router")

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = str((interaction.data or {}).get("custom_id") or "")
        if not custom_id:
            return
        handled = custom_id.startswith(("cf:", "bal:", "profile:", "settings:", "pf:", "rain:", "verify:"))
        if not handled:
            return
        try:
            if custom_id == self._ids.DEPOSIT:
                await self._on_deposit(interaction)
            elif custom_id == self._ids.WITHDRAW:
                await self._on_withdraw(interaction)
            elif custom_id == self._ids.CURRENCY:
                await self._on_currency(interaction)
            elif custom_id == self._ids.VERIFY_START:
                await self._on_verify_start(interaction)
            elif custom_id == self._ids.VERIFY_ENTER:
                await interaction.response.send_modal(
                    VerifyCodeModal(self._container, self._embeds, self._errors)
                )
            elif custom_id == self._ids.PF_CHECK:
                await interaction.response.send_modal(
                    ProvablyFairCheckModal(self._container, self._embeds, self._errors)
                )
            elif (game_id := self._ids.parse_verify_game(custom_id)):
                await self._on_verify_game(interaction, game_id)
            elif (game_id := self._ids.parse_join_heads(custom_id)):
                await self._on_join(interaction, game_id, CoinSide.HEADS)
            elif (game_id := self._ids.parse_join_tails(custom_id)):
                await self._on_join(interaction, game_id, CoinSide.TAILS)
            elif (game_id := self._ids.parse_ready(custom_id)):
                await self._on_ready(interaction, game_id)
            elif (game_id := self._ids.parse_call_bot(custom_id)):
                await self._on_call_bot(interaction, game_id)
            elif (game_id := self._ids.parse_join(custom_id)):
                await self._on_join(interaction, game_id, None)
            elif (game_id := self._ids.parse_leave(custom_id)):
                await self._on_leave(interaction, game_id)
            elif (game_id := self._ids.parse_rerun(custom_id)):
                await self._on_rerun(interaction, game_id)
            elif (game_id := self._ids.parse_accept(custom_id)):
                await self._on_accept_rematch(interaction, game_id)
            elif (game_id := self._ids.parse_decline(custom_id)):
                await self._on_decline_rematch(interaction, game_id)
            elif (game_id := self._ids.parse_cancel(custom_id)):
                await self._on_cancel(interaction, game_id)
            elif (rain_id := self._ids.parse_rain_join(custom_id)):
                await self._on_rain_join(interaction, rain_id)
            elif (target := self._ids.parse_history(custom_id)):
                await self._on_history(interaction, target.user_id, target.page)
        except AppError as exc:
            self._logger.info("component_rejected", customId=custom_id, code=exc.code)
            await self._send_panel(interaction, self._errors.panel_for(exc), ephemeral=True)
        except Exception as exc:
            self._logger.exception("component_failed", customId=custom_id)
            await self._send_panel(interaction, self._errors.panel_for(exc), ephemeral=True)

    async def _send_panel(
        self,
        interaction: discord.Interaction,
        panel: CasinoPanel,
        *,
        ephemeral: bool,
    ) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(**panel.send_kwargs(ephemeral=ephemeral))
            else:
                await interaction.response.send_message(**panel.send_kwargs(ephemeral=ephemeral))
        except discord.HTTPException as exc:
            if exc.code == 10003:
                return
            raise

    async def _on_verify_start(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                **self._embeds.error("Verify in the server.").send_kwargs(ephemeral=True)
            )
            return
        await self._container.rate_limits.enforce(f"verify:{interaction.user.id}")
        config = await self._container.guild_configs.get(str(interaction.guild.id))
        role = interaction.guild.get_role(int(config.member_role_id)) if config.member_role_id else None
        if role is None:
            await interaction.response.send_message(
                **self._embeds.error("Verification is not configured. Ask an admin.").send_kwargs(ephemeral=True)
            )
            return
        if role in interaction.user.roles:
            await interaction.response.send_message(
                **self._embeds.success("Already verified", "You already have the Member role.").send_kwargs(
                    ephemeral=True
                )
            )
            return
        code = self._container.verification.issue(str(interaction.guild.id), str(interaction.user.id))
        await interaction.response.send_message(
            **self._embeds.verify_challenge(code).send_kwargs(ephemeral=True)
        )

    async def _on_deposit(self, interaction: discord.Interaction) -> None:
        if not self._container.deposits.is_configured():
            await interaction.response.send_message(
                **self._embeds.deposits_unavailable().send_kwargs(ephemeral=True)
            )
            return
        user = await self._container.users.get_or_create(str(interaction.user.id))
        await interaction.response.send_message(
            **self._embeds.deposit(user).send_kwargs(ephemeral=True)
        )

    async def _on_withdraw(self, interaction: discord.Interaction) -> None:
        user = await self._container.users.get_or_create(str(interaction.user.id))
        quote = await self._container.price.get_quote(allow_stale=True)
        currency = user.display_currency
        balance = self._container.math.sol_to_fiat(user.sol_balance, quote.sol_price(currency))
        balance_text = self._container.math.format_fiat_symbol(balance, currency)
        await interaction.response.send_modal(
            WithdrawModal(
                self._container,
                self._errors,
                currency=currency,
                balance_text=balance_text,
            )
        )

    async def _on_currency(self, interaction: discord.Interaction) -> None:
        values = (interaction.data or {}).get("values") or []
        if not values:
            await interaction.response.send_message(
                **self._embeds.error("Select a currency.").send_kwargs(ephemeral=True)
            )
            return
        user = await self._container.settings_service.update_currency(
            str(interaction.user.id), str(values[0])
        )
        await interaction.response.send_message(
            **self._embeds.success("Currency updated", f"Bets will use **{user.display_currency}**.").send_kwargs(
                ephemeral=True
            )
        )

    async def _on_join(
        self,
        interaction: discord.Interaction,
        game_id: str,
        side: CoinSide | None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        with LogContext.bind(userId=str(interaction.user.id), gameId=game_id):
            game = await self._container.games.join_game(game_id, str(interaction.user.id), side)
            await self._tables().on_seat_taken(game, interaction.user, interaction=interaction)
            await self._send_panel(interaction, self._embeds.joined_table(game), ephemeral=True)
            if game.is_awaiting_settlement() or game.status is GameStatus.COMPLETED:
                try:
                    await self._tables().on_resolved(game, interaction=interaction)
                except Exception:
                    self._logger.exception("resolved_discord_failed", gameId=game_id)
            elif game.status is GameStatus.ACTIVE:
                await self._tables().on_dice_started(game)

    async def _on_ready(self, interaction: discord.Interaction, game_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        with LogContext.bind(userId=str(interaction.user.id), gameId=game_id):
            game = await self._container.games.mark_ready(game_id, str(interaction.user.id))
            if game.is_awaiting_settlement() or game.status is GameStatus.COMPLETED:
                try:
                    await self._tables().on_resolved(game, interaction=interaction)
                except Exception:
                    self._logger.exception("resolved_discord_failed", gameId=game_id)
                return
            if game.status is GameStatus.ACTIVE:
                await self._tables().on_dice_started(game)
                return
            await self._tables().on_lobby_changed(game, interaction=interaction)

    async def _on_call_bot(self, interaction: discord.Interaction, game_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        with LogContext.bind(userId=str(interaction.user.id), gameId=game_id):
            game = await self._container.games.call_bot(game_id, str(interaction.user.id))
            if game.is_awaiting_settlement() or game.status is GameStatus.COMPLETED:
                await self._tables().on_resolved(game, interaction=interaction)
                return
            if game.status is GameStatus.ACTIVE:
                await self._tables().on_dice_started(game)
                return
            await self._tables().on_lobby_changed(game, interaction=interaction)

    async def _on_verify_game(self, interaction: discord.Interaction, game_id: str) -> None:
        game = await self._container.games.get(game_id)
        await interaction.response.send_message(
            **self._embeds.provably_fair(game).send_kwargs(ephemeral=True)
        )

    async def _on_rerun(self, interaction: discord.Interaction, game_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        with LogContext.bind(userId=str(interaction.user.id), gameId=game_id):
            rematch = await self._container.games.start_rematch(game_id, str(interaction.user.id))
            await self._tables().on_rematch_opened(rematch)
            if rematch.is_awaiting_rematch_accepts():
                await interaction.followup.send(
                    **self._embeds.success(
                        "Rematch posted",
                        "The other players can Accept or Decline in this channel.",
                    ).send_kwargs(ephemeral=True)
                )
                return
            await interaction.followup.send(
                **self._embeds.success("Rematch started", "Press **Ready** when you want to flip.").send_kwargs(
                    ephemeral=True
                )
            )

    async def _on_accept_rematch(self, interaction: discord.Interaction, game_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        with LogContext.bind(userId=str(interaction.user.id), gameId=game_id):
            game = await self._container.games.accept_rematch(game_id, str(interaction.user.id))
            await self._tables().on_seat_taken(game, interaction.user, interaction=interaction)
            await interaction.followup.send(
                **self._embeds.joined_table(game).send_kwargs(ephemeral=True)
            )

    async def _on_decline_rematch(self, interaction: discord.Interaction, game_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        with LogContext.bind(userId=str(interaction.user.id), gameId=game_id):
            game = await self._container.games.decline_rematch(game_id, str(interaction.user.id))
            await self._send_panel(
                interaction,
                self._embeds.success(
                    "Rematch declined",
                    "Everyone who paid for the rematch was refunded and this table was closed.",
                ),
                ephemeral=True,
            )
            await self._tables().on_table_cleared(game, interaction=interaction)

    async def _on_leave(self, interaction: discord.Interaction, game_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        player_id = str(interaction.user.id)
        game = await self._container.games.leave_game(game_id, player_id)
        emptied = game.status is GameStatus.CANCELLED or not game.human_player_ids()
        refund = self._container.math.format_sol(game.wager_for(player_id))
        await self._send_panel(
            interaction,
            self._embeds.success("Left table", f"Your wager of `{refund} SOL` was refunded."),
            ephemeral=True,
        )
        if emptied:
            await self._tables().on_table_cleared(game, interaction=interaction)
        else:
            await self._tables().on_player_left(game, interaction.user, interaction=interaction)

    async def _on_cancel(self, interaction: discord.Interaction, game_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        game = await self._container.games.cancel_game(game_id, str(interaction.user.id))
        await self._send_panel(interaction, self._embeds.cancelled_game(game), ephemeral=True)
        await self._tables().on_cancelled(game, interaction=interaction)

    async def _on_rain_join(self, interaction: discord.Interaction, rain_id: str) -> None:
        await interaction.response.defer(ephemeral=True)
        with LogContext.bind(userId=str(interaction.user.id), rainId=rain_id):
            rain = await self._container.rains.join_rain(rain_id, str(interaction.user.id))
            await self._rains().on_joined(rain)
            await interaction.followup.send(
                **self._embeds.success(
                    "Joined the rain",
                    "You will receive an equal share when the timer ends.",
                ).send_kwargs(ephemeral=True)
            )

    async def _on_history(self, interaction: discord.Interaction, user_id: str, page: int) -> None:
        page_size = 10
        games, total = await self._container.games.history_for(user_id, page=page, limit=page_size)
        await interaction.response.send_message(
            **self._embeds.history(user_id, games, page, total=total, page_size=page_size).send_kwargs(
                ephemeral=True
            )
        )

    def _tables(self):
        from casino.discord_bot.bot import CasinoBot

        if not isinstance(self.bot, CasinoBot):
            raise RuntimeError("Casino bot is not ready.")
        return self.bot.tables

    def _rains(self):
        from casino.discord_bot.bot import CasinoBot

        if not isinstance(self.bot, CasinoBot):
            raise RuntimeError("Casino bot is not ready.")
        return self.bot.rains
