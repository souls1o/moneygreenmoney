from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import discord

from casino.container import AppContainer
from casino.discord_bot.installs import is_home_guild
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.discord_bot.presentation.errors import DiscordErrorPresenter
from casino.domain.enums import CoinSide, GameMode, GameStatus, GameType
from casino.errors import GameError
from casino.logging.logger import LogContext


@dataclass(slots=True)
class GameSetupState:
    user_id: str
    game_type: GameType
    fiat: Decimal
    currency: str
    amount_usd: Decimal
    matchup: str = "2"
    side: str = "heads"
    first_to: int = 1
    mines: int = 3
    vs_house: bool = False


def _setup_builder(embeds: EmbedFactory, state: GameSetupState):
    amount = embeds._container.math.format_fiat_symbol(state.fiat, state.currency)
    builder = embeds.builder(
        _TITLES[state.game_type],
        description="Only you can see this. Set the rules, then **Create table**.",
    ).field("Wager", amount)
    if state.game_type is GameType.BLACKJACK:
        return builder.timeout(300)
    if state.game_type is GameType.MINES:
        return builder.field("Mines", str(state.mines)).timeout(300)
    builder.field("Matchup", matchup_label(state.matchup))
    if state.game_type is GameType.COINFLIP:
        builder.field("Your side", state.side.title())
    if state.game_type in {GameType.COINFLIP, GameType.DICE}:
        builder.field("First to", str(state.first_to))
    return builder.timeout(300)


_TITLES = {
    GameType.COINFLIP: "New coinflip",
    GameType.DICE: "New dice table",
    GameType.BLACKJACK: "New blackjack",
    GameType.TARGET: "New target",
    GameType.MINES: "New mines",
}

_COINFLIP_MATCHUPS = (("2", "1v1"), ("22", "2v2"))
_DICE_MATCHUPS = (("2", "1v1"), ("3", "1v1v1"), ("4", "1v1v1v1"), ("22", "2v2"))
_TARGET_MATCHUPS = (("2", "1v1"), ("3", "1v1v1"), ("4", "1v1v1v1"))


def matchup_label(value: str) -> str:
    return {"2": "1v1", "3": "1v1v1", "4": "1v1v1v1", "22": "2v2"}.get(value, "1v1")


class GameSetupView(discord.ui.LayoutView):
    def __init__(
        self,
        container: AppContainer,
        embeds: EmbedFactory,
        errors: DiscordErrorPresenter,
        state: GameSetupState,
        *,
        channel: discord.abc.Messageable | None,
    ) -> None:
        super().__init__(timeout=300)
        self._container = container
        self._embeds = embeds
        self._errors = errors
        self.state = state
        self._channel = channel
        self.render()

    def render(self) -> None:
        builder = _setup_builder(self._embeds, self.state)
        if self.state.game_type is GameType.COINFLIP:
            builder.add_item(_MatchupSelect(self, _COINFLIP_MATCHUPS))
            builder.add_item(_SideSelect(self))
            builder.add_item(_FirstToSelect(self))
        elif self.state.game_type is GameType.DICE:
            builder.add_item(_MatchupSelect(self, _DICE_MATCHUPS))
            builder.add_item(_FirstToSelect(self))
        elif self.state.game_type is GameType.TARGET:
            builder.add_item(_MatchupSelect(self, _TARGET_MATCHUPS))
        elif self.state.game_type is GameType.MINES:
            builder.add_item(_MinesSelect(self))
        builder.button(
            "Create table",
            style=discord.ButtonStyle.success,
            callback=self._on_create,
        )
        builder.apply_to(self)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        self.render()
        await interaction.response.edit_message(view=self)

    async def _on_create(self, interaction: discord.Interaction) -> None:
        if str(interaction.user.id) != self.state.user_id:
            await interaction.response.send_message(
                **self._embeds.error("This setup isn't yours.").send_kwargs(ephemeral=True)
            )
            return
        await interaction.response.defer()
        user_id = self.state.user_id
        if self.state.game_type is GameType.MINES:
            await start_mines(
                interaction,
                self._container,
                self._embeds,
                self._errors,
                user_id=user_id,
                fiat=self.state.fiat,
                mines=self.state.mines,
            )
            return
        game_id: str | None = None
        posted = False
        with LogContext.bind(userId=user_id):
            try:
                game = await self._create_game(interaction)
                game_id = game.game_id
                if self.state.vs_house and game.game_type is not GameType.BLACKJACK:
                    game = await self._fill_house(game)
                from casino.discord_bot.bot import CasinoBot

                bot = interaction.client
                if not isinstance(bot, CasinoBot):
                    raise RuntimeError("Casino bot is not ready.")
                refreshed, message = await self._provision(bot, interaction, game)
                posted = True
                if refreshed.status is GameStatus.COMPLETED:
                    await bot.tables.on_resolved(refreshed)
                await interaction.edit_original_response(
                    **self._success_panel(refreshed, message).edit_kwargs()
                )
            except Exception as exc:
                if game_id is not None and not posted:
                    try:
                        await self._container.games.cancel_game(game_id, user_id, admin=True)
                    except Exception:
                        pass
                await interaction.followup.send(**self._errors.panel_for(exc).send_kwargs(ephemeral=True))

    async def _create_game(self, interaction: discord.Interaction):
        state = self.state
        mode = GameMode.TEAM if state.matchup == "22" else GameMode.SOLO
        max_players = 4 if mode is GameMode.TEAM else int(state.matchup)
        await self._require_listing(interaction, state.game_type)
        return await self._container.games.create_game(
            player1_id=state.user_id,
            fiat_amount=state.fiat,
            side=CoinSide(state.side),
            mode=mode,
            game_type=state.game_type,
            guild_id=str(interaction.guild_id) if interaction.guild_id else None,
            max_players=max_players,
            first_to=state.first_to,
        )

    async def _fill_house(self, game):
        current = game
        try:
            while not current.is_full():
                current = await self._container.games.call_bot(current.game_id, self.state.user_id)
        except GameError:
            return current
        return current

    async def _require_listing(self, interaction: discord.Interaction, game_type: GameType) -> None:
        if game_type in {GameType.BLACKJACK, GameType.TARGET, GameType.MINES}:
            return
        home = is_home_guild(self._container.settings, interaction.guild_id)
        if game_type is GameType.COINFLIP and not home:
            return
        if interaction.guild_id is None:
            raise GameError("This command must be used in a server.")
        config = await self._container.guild_configs.get(str(interaction.guild_id))
        if config.listing_channel_id(game_type):
            return
        names = {
            GameType.COINFLIP: "open coinflips",
            GameType.DICE: "open dice",
        }
        raise GameError(f"Ask an admin to set the {names.get(game_type, 'open games')} channel in `/admin config`.")

    async def _provision(self, bot, interaction: discord.Interaction, game):
        home = is_home_guild(self._container.settings, interaction.guild_id)
        if game.is_blackjack():
            channel = self._channel
            if channel is None or not isinstance(channel, discord.abc.Messageable):
                raise GameError("This command must be used in a text channel.")
            refreshed = await bot.tables.provision_here(game, channel)
            return refreshed, f"<#{refreshed.channel_id}>" if refreshed.channel_id else "this channel"
        if game.is_target() or (game.game_type is GameType.COINFLIP and not home):
            channel = self._channel
            if channel is None or not isinstance(channel, discord.abc.Messageable):
                raise GameError("This command must be used in a channel.")
            refreshed = await bot.tables.provision_public(game, channel)
            return refreshed, "this channel"
        refreshed = await bot.tables.provision(game, interaction.user)
        listing = f"<#{refreshed.channel_id}>" if refreshed.channel_id else "the open games channel"
        if refreshed.private_channel_id:
            listing = f"{listing}. Table: <#{refreshed.private_channel_id}>"
        return refreshed, listing

    def _success_panel(self, game, listing: str):
        title = "Table live"
        if game.is_blackjack():
            return self._embeds.success(title, f"Dealt in {listing}.")
        extra = ""
        if game.is_dice():
            extra = f" First to **{game.first_to or 1}**. Press **Ready**, then `/roll` on your turn."
        elif game.is_target():
            extra = " Lock a number from 1 to 999. Closest to the draw wins."
        elif game.game_type is GameType.COINFLIP:
            extra = " Press **Ready** when everyone is seated."
            if (game.first_to or 1) > 1:
                extra = f" First to **{game.first_to}**.{extra}"
        return self._embeds.success(title, f"Listed in {listing}.{extra}")


class _OwnerSelect(discord.ui.Select):
    def __init__(self, view: GameSetupView, *, placeholder: str, options: list[discord.SelectOption]) -> None:
        super().__init__(placeholder=placeholder, options=options, min_values=1, max_values=1)
        self._setup = view

    async def callback(self, interaction: discord.Interaction) -> None:
        if str(interaction.user.id) != self._setup.state.user_id:
            await interaction.response.send_message(
                **self._setup._embeds.error("This setup isn't yours.").send_kwargs(ephemeral=True)
            )
            return
        await self._apply(str(self.values[0]))
        await self._setup._refresh(interaction)

    async def _apply(self, value: str) -> None:
        raise NotImplementedError


class _MatchupSelect(_OwnerSelect):
    def __init__(self, view: GameSetupView, choices: tuple[tuple[str, str], ...]) -> None:
        options = [
            discord.SelectOption(label=label, value=value, default=view.state.matchup == value)
            for value, label in choices
        ]
        super().__init__(view, placeholder="Matchup", options=options)

    async def _apply(self, value: str) -> None:
        self._setup.state.matchup = value


class _SideSelect(_OwnerSelect):
    def __init__(self, view: GameSetupView) -> None:
        options = [
            discord.SelectOption(label="Heads", value="heads", default=view.state.side == "heads"),
            discord.SelectOption(label="Tails", value="tails", default=view.state.side == "tails"),
        ]
        super().__init__(view, placeholder="Your side", options=options)

    async def _apply(self, value: str) -> None:
        self._setup.state.side = value


class _FirstToSelect(_OwnerSelect):
    def __init__(self, view: GameSetupView) -> None:
        options = [
            discord.SelectOption(
                label=str(value),
                value=str(value),
                default=view.state.first_to == value,
            )
            for value in range(1, 22)
        ]
        super().__init__(view, placeholder="First to", options=options)

    async def _apply(self, value: str) -> None:
        self._setup.state.first_to = int(value)


class _MinesSelect(_OwnerSelect):
    def __init__(self, view: GameSetupView) -> None:
        options = [
            discord.SelectOption(
                label=str(value),
                value=str(value),
                default=view.state.mines == value,
            )
            for value in range(1, 25)
        ]
        super().__init__(view, placeholder="Mines", options=options)

    async def _apply(self, value: str) -> None:
        self._setup.state.mines = int(value)


async def open_game_setup(
    interaction: discord.Interaction,
    container: AppContainer,
    embeds: EmbedFactory,
    errors: DiscordErrorPresenter,
    *,
    game_type: GameType,
    amount: str,
) -> None:
    if interaction.channel is None:
        await interaction.response.send_message(
            **embeds.error("This command must be used in a channel.").send_kwargs(ephemeral=True)
        )
        return
    user_id = str(interaction.user.id)
    try:
        fiat = container.validation.parse_fiat_amount(amount)
        user = await container.users.get_or_create(user_id)
        quote = await container.price.get_quote()
        currency_price = quote.sol_price(user.display_currency)
        amount_usd = fiat * quote.sol_price("USD") / currency_price
        container.validation.validate_bet(amount_usd)
    except Exception as exc:
        await interaction.response.send_message(**errors.panel_for(exc).send_kwargs(ephemeral=True))
        return
    if game_type is GameType.BLACKJACK:
        await _start_blackjack(
            interaction,
            container,
            embeds,
            errors,
            user_id=user_id,
            fiat=fiat,
        )
        return
    if game_type is GameType.LIMBO:
        await start_limbo(
            interaction,
            container,
            embeds,
            errors,
            user_id=user_id,
            fiat=fiat,
            multiplier=Decimal("2"),
        )
        return
    state = GameSetupState(
        user_id=user_id,
        game_type=game_type,
        fiat=fiat,
        currency=user.display_currency,
        amount_usd=amount_usd,
        vs_house=game_type is GameType.BLACKJACK,
    )
    view = GameSetupView(
        container,
        embeds,
        errors,
        state,
        channel=interaction.channel if isinstance(interaction.channel, discord.abc.Messageable) else None,
    )
    await interaction.response.send_message(view=view, ephemeral=True)


async def _start_blackjack(
    interaction: discord.Interaction,
    container: AppContainer,
    embeds: EmbedFactory,
    errors: DiscordErrorPresenter,
    *,
    user_id: str,
    fiat: Decimal,
) -> None:
    await interaction.response.defer()
    game_id: str | None = None
    posted = False
    with LogContext.bind(userId=user_id):
        try:
            game = await container.games.create_game(
                player1_id=user_id,
                fiat_amount=fiat,
                side=CoinSide.HEADS,
                game_type=GameType.BLACKJACK,
                guild_id=str(interaction.guild_id) if interaction.guild_id else None,
            )
            game_id = game.game_id
            from casino.discord_bot.bot import CasinoBot

            bot = interaction.client
            if not isinstance(bot, CasinoBot):
                raise RuntimeError("Casino bot is not ready.")
            refreshed = await bot.tables.provision_blackjack_interaction(game, interaction)
            posted = True
            if refreshed.status is GameStatus.COMPLETED:
                await bot.tables.on_resolved(refreshed, interaction=interaction)
        except Exception as exc:
            if game_id is not None and not posted:
                try:
                    await container.games.cancel_game(game_id, user_id, admin=True)
                except Exception:
                    pass
            await interaction.followup.send(**errors.panel_for(exc).send_kwargs(ephemeral=True))


async def start_limbo(
    interaction: discord.Interaction,
    container: AppContainer,
    embeds: EmbedFactory,
    errors: DiscordErrorPresenter,
    *,
    user_id: str,
    fiat: Decimal,
    multiplier: Decimal,
) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer()
    game_id: str | None = None
    posted = False
    with LogContext.bind(userId=user_id):
        try:
            game = await container.games.create_game(
                player1_id=user_id,
                fiat_amount=fiat,
                side=CoinSide.HEADS,
                game_type=GameType.LIMBO,
                guild_id=str(interaction.guild_id) if interaction.guild_id else None,
                limbo_target=multiplier,
            )
            game_id = game.game_id
            from casino.discord_bot.bot import CasinoBot

            bot = interaction.client
            if not isinstance(bot, CasinoBot):
                raise RuntimeError("Casino bot is not ready.")
            refreshed = await bot.tables.provision_limbo_interaction(game, interaction)
            posted = True
            if refreshed.status is GameStatus.COMPLETED:
                await bot.tables.on_resolved(refreshed, interaction=interaction)
        except Exception as exc:
            if game_id is not None and not posted:
                try:
                    await container.games.cancel_game(game_id, user_id, admin=True)
                except Exception:
                    pass
            await interaction.followup.send(**errors.panel_for(exc).send_kwargs(ephemeral=True))


async def start_mines(
    interaction: discord.Interaction,
    container: AppContainer,
    embeds: EmbedFactory,
    errors: DiscordErrorPresenter,
    *,
    user_id: str,
    fiat: Decimal,
    mines: int,
) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer()
    game_id: str | None = None
    posted = False
    with LogContext.bind(userId=user_id):
        try:
            game = await container.games.create_game(
                player1_id=user_id,
                fiat_amount=fiat,
                side=CoinSide.HEADS,
                game_type=GameType.MINES,
                guild_id=str(interaction.guild_id) if interaction.guild_id else None,
                mines_count=mines,
            )
            game_id = game.game_id
            from casino.discord_bot.bot import CasinoBot

            bot = interaction.client
            if not isinstance(bot, CasinoBot):
                raise RuntimeError("Casino bot is not ready.")
            refreshed = await bot.tables.provision_mines_interaction(game, interaction)
            posted = True
            if refreshed.status is GameStatus.COMPLETED:
                await bot.tables.on_resolved(refreshed, interaction=interaction)
        except Exception as exc:
            if game_id is not None and not posted:
                try:
                    await container.games.cancel_game(game_id, user_id, admin=True)
                except Exception:
                    pass
            await interaction.followup.send(**errors.panel_for(exc).send_kwargs(ephemeral=True))
