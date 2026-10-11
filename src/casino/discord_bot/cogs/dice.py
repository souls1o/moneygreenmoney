from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.discord_bot.presentation.errors import DiscordErrorPresenter
from casino.domain.enums import CoinSide, GameMode, GameType
from casino.logging.logger import LogContext


class DiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot, container: AppContainer) -> None:
        self.bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)
        self._errors = DiscordErrorPresenter()

    @app_commands.command(
        name="dice",
        description="Create a first-to dice table. Amount is in your settings currency.",
    )
    @app_commands.describe(
        amount="Bet amount in your selected currency (for example 5 for 5 EUR)",
        players="Matchup: 1v1, 1v1v1, 1v1v1v1, or 2v2",
        first_to="Points needed to win",
    )
    @app_commands.choices(
        players=[
            app_commands.Choice(name="1v1", value=2),
            app_commands.Choice(name="1v1v1", value=3),
            app_commands.Choice(name="1v1v1v1", value=4),
            app_commands.Choice(name="2v2", value=22),
        ]
    )
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def dice(
        self,
        interaction: discord.Interaction,
        amount: str,
        players: app_commands.Choice[int],
        first_to: app_commands.Range[int, 1, 21],
    ) -> None:
        await self._container.rate_limits.enforce(f"dice:{interaction.user.id}")
        if interaction.channel is None:
            await interaction.response.send_message(
                **self._embeds.error("This command must be used in a channel.").send_kwargs(ephemeral=True)
            )
            return
        await interaction.response.defer(ephemeral=True)
        user_id = str(interaction.user.id)
        game_id: str | None = None
        posted = False
        with LogContext.bind(userId=user_id):
            try:
                if interaction.guild_id:
                    config = await self._container.guild_configs.get(str(interaction.guild_id))
                    if not config.listing_channel_id(GameType.DICE):
                        await interaction.followup.send(
                            **self._embeds.error(
                                "Ask an admin to set the open dice channel in `/admin config`."
                            ).send_kwargs(ephemeral=True)
                        )
                        return
                fiat = self._container.validation.parse_fiat_amount(amount)
                user = await self._container.users.get_or_create(user_id)
                quote = await self._container.price.get_quote()
                currency_price = quote.sol_price(user.display_currency)
                amount_usd = fiat * quote.sol_price("USD") / currency_price
                self._container.validation.validate_bet(amount_usd)
                team_table = int(players.value) == 22
                game = await self._container.games.create_game(
                    player1_id=user_id,
                    fiat_amount=fiat,
                    side=CoinSide.HEADS,
                    mode=GameMode.TEAM if team_table else GameMode.SOLO,
                    game_type=GameType.DICE,
                    guild_id=str(interaction.guild_id) if interaction.guild_id else None,
                    max_players=4 if team_table else int(players.value),
                    first_to=first_to,
                )
                game_id = game.game_id
                from casino.discord_bot.bot import CasinoBot

                bot = interaction.client
                if not isinstance(bot, CasinoBot):
                    raise RuntimeError("Casino bot is not ready.")
                refreshed = await bot.tables.provision(game, interaction.user)
                posted = True
                listing = f"<#{refreshed.channel_id}>" if refreshed.channel_id else "open dice"
                await interaction.followup.send(
                    **self._embeds.success(
                        "Table live",
                        f"Listed in {listing}. First to **{first_to}**"
                        + (" — Team 1 vs Team 2." if team_table else ".")
                        + " Press **Ready** in the private table, then `/roll` on your turn.",
                    ).send_kwargs(ephemeral=True)
                )
            except Exception as exc:
                if game_id is not None and not posted:
                    try:
                        await self._container.games.cancel_game(game_id, user_id, admin=True)
                    except Exception:
                        pass
                await interaction.followup.send(**self._errors.panel_for(exc).send_kwargs(ephemeral=True))

    @app_commands.command(name="roll", description="Roll 2 dice on your turn in the active dice table.")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def roll(self, interaction: discord.Interaction) -> None:
        await self._container.rate_limits.enforce(f"roll:{interaction.user.id}")
        channel_id = str(interaction.channel_id) if interaction.channel_id else ""
        await interaction.response.defer(ephemeral=True)
        with LogContext.bind(userId=str(interaction.user.id)):
            try:
                from casino.discord_bot.bot import CasinoBot

                bot = interaction.client
                if isinstance(bot, CasinoBot):
                    result = await bot.tables.handle_player_roll(channel_id, str(interaction.user.id))
                else:
                    result = await self._container.games.roll_dice(channel_id, str(interaction.user.id))
                turn = result.game.current_turn_id()
                if result.game.status.value == "completed":
                    message = "The match is over."
                elif turn:
                    message = f"Next turn: <@{turn}>" if not turn.startswith("HOUSE") else "House is rolling."
                else:
                    message = "Roll recorded."
                await interaction.followup.send(
                    **self._embeds.success("Rolled", message).send_kwargs(ephemeral=True)
                )
            except Exception as exc:
                await interaction.followup.send(**self._errors.panel_for(exc).send_kwargs(ephemeral=True))
