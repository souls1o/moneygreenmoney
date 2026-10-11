from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from casino.container import AppContainer
from casino.discord_bot.installs import is_home_guild
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.discord_bot.presentation.errors import DiscordErrorPresenter
from casino.domain.enums import CoinSide, GameMode, GameType
from casino.logging.logger import LogContext


class CoinflipCog(commands.Cog):
    def __init__(self, bot: commands.Bot, container: AppContainer) -> None:
        self.bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)
        self._errors = DiscordErrorPresenter()

    @app_commands.command(
        name="coinflip",
        description="Create a PvP coinflip. Amount is in your settings currency.",
    )
    @app_commands.describe(
        amount="Bet amount in your selected currency (for example 5 for 5 EUR)",
        mode="1v1 or 2v2",
        side="Heads or Tails",
        first_to="Coin wins needed. Each flip starts 3 seconds after the last",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="1v1", value="solo"),
            app_commands.Choice(name="2v2", value="team"),
        ],
        side=[
            app_commands.Choice(name="Heads", value="heads"),
            app_commands.Choice(name="Tails", value="tails"),
        ],
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def coinflip(
        self,
        interaction: discord.Interaction,
        amount: str,
        mode: app_commands.Choice[str],
        side: app_commands.Choice[str],
        first_to: app_commands.Range[int, 1, 21] = 1,
    ) -> None:
        await self._container.rate_limits.enforce(f"coinflip:{interaction.user.id}")
        home = is_home_guild(self._container.settings, interaction.guild_id)
        if home and interaction.channel is None:
            await interaction.response.send_message(
                **self._embeds.error("This command must be used in a channel.").send_kwargs(ephemeral=True)
            )
            return
        if not home and interaction.channel_id is None:
            await interaction.response.send_message(
                **self._embeds.error("This command must be used in a channel.").send_kwargs(ephemeral=True)
            )
            return
        await interaction.response.defer(ephemeral=home)
        user_id = str(interaction.user.id)
        game_id: str | None = None
        posted = False
        with LogContext.bind(userId=user_id):
            try:
                if home:
                    config = await self._container.guild_configs.get(str(interaction.guild_id))
                    if not config.listing_channel_id(GameType.COINFLIP):
                        await interaction.followup.send(
                            **self._embeds.error(
                                "Ask an admin to set the open coinflips channel in `/admin config`."
                            ).send_kwargs(ephemeral=True)
                        )
                        return
                fiat = self._container.validation.parse_fiat_amount(amount)
                user = await self._container.users.get_or_create(user_id)
                quote = await self._container.price.get_quote()
                currency_price = quote.sol_price(user.display_currency)
                amount_usd = fiat * quote.sol_price("USD") / currency_price
                self._container.validation.validate_bet(amount_usd)
                game = await self._container.games.create_game(
                    player1_id=user_id,
                    fiat_amount=fiat,
                    side=CoinSide(side.value),
                    mode=GameMode(mode.value),
                    guild_id=str(interaction.guild_id) if interaction.guild_id else None,
                    first_to=first_to,
                )
                game_id = game.game_id
                from casino.discord_bot.bot import CasinoBot

                bot = interaction.client
                if not isinstance(bot, CasinoBot):
                    raise RuntimeError("Casino bot is not ready.")
                if home:
                    refreshed = await bot.tables.provision(game, interaction.user)
                    posted = True
                    listing = f"<#{refreshed.channel_id}>" if refreshed.channel_id else "open coinflips"
                    table = (
                        f" Open it here: <#{refreshed.private_channel_id}>."
                        if refreshed.private_channel_id
                        else " Press **Ready** in the private table when everyone is seated."
                    )
                    first = f" First to **{first_to}**." if first_to > 1 else ""
                    await interaction.followup.send(
                        **self._embeds.success(
                            "Table live",
                            f"Listed in {listing}.{first}{table}",
                        ).send_kwargs(ephemeral=True)
                    )
                    return
                await bot.tables.provision_inline(game, interaction)
                posted = True
            except Exception as exc:
                if game_id is not None and not posted:
                    try:
                        await self._container.games.cancel_game(game_id, user_id, admin=True)
                    except Exception:
                        pass
                if not home:
                    try:
                        await interaction.delete_original_response()
                    except discord.HTTPException:
                        pass
                await interaction.followup.send(**self._errors.panel_for(exc).send_kwargs(ephemeral=True))
