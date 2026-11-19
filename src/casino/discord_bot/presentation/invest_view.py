from __future__ import annotations

import discord

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.discord_bot.presentation.errors import DiscordErrorPresenter
from casino.discord_bot.presentation.modals import InvestAmountModal
from casino.domain.investor import InvestorPosition
from casino.domain.records import UserRecord
from casino.errors import InvestorError, PermissionDeniedError
from casino.services.price_service import PriceQuote
from casino.utils.money import SolAmount


async def require_investor_role(
    interaction: discord.Interaction,
    container: AppContainer,
) -> None:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        raise InvestorError("Use /invest in the casino server.")
    config = await container.guild_configs.get(str(interaction.guild.id))
    if not config.investor_role_id:
        raise InvestorError("Ask an admin to set the Investor role in /admin config.")
    role = interaction.guild.get_role(int(config.investor_role_id))
    if role is None:
        raise InvestorError("The Investor role is missing. Ask an admin to set it again.")
    if role not in interaction.user.roles:
        raise PermissionDeniedError("The Investor role is required to use /invest.")


class _InvestButton(discord.ui.Button):
    def __init__(
        self,
        container: AppContainer,
        embeds: EmbedFactory,
        errors: DiscordErrorPresenter,
        *,
        withdraw: bool,
        currency: str,
        available_text: str,
    ) -> None:
        super().__init__(
            label="Withdraw" if withdraw else "Deposit",
            style=discord.ButtonStyle.secondary if withdraw else discord.ButtonStyle.success,
        )
        self._container = container
        self._embeds = embeds
        self._errors = errors
        self._withdraw = withdraw
        self._currency = currency
        self._available_text = available_text

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            await require_investor_role(interaction, self._container)
        except Exception as exc:
            await interaction.response.send_message(
                **self._errors.panel_for(exc).send_kwargs(ephemeral=True)
            )
            return
        await interaction.response.send_modal(
            InvestAmountModal(
                self._container,
                self._embeds,
                self._errors,
                currency=self._currency,
                available_text=self._available_text,
                withdraw=self._withdraw,
            )
        )


def build_invest_panel(
    container: AppContainer,
    embeds: EmbedFactory,
    errors: DiscordErrorPresenter,
    member: discord.abc.User,
    user: UserRecord,
    position: InvestorPosition,
    quote: PriceQuote,
):
    math = container.math
    currency = user.display_currency
    price = quote.sol_price(currency)
    deposit_cap = math.format_fiat_symbol(
        math.sol_to_fiat(SolAmount.from_lamports(position.player_balance_lamports), price),
        currency,
    )
    withdraw_cap = math.format_fiat_symbol(
        math.sol_to_fiat(
            SolAmount.from_lamports(min(position.user_equity_lamports, position.book_lamports)),
            price,
        ),
        currency,
    )
    builder = embeds.invest(member, position, quote, currency)
    builder.button(
        "Deposit",
        style=discord.ButtonStyle.success,
        callback=_InvestButton(
            container,
            embeds,
            errors,
            withdraw=False,
            currency=currency,
            available_text=deposit_cap,
        ).callback,
    )
    builder.button(
        "Withdraw",
        style=discord.ButtonStyle.secondary,
        callback=_InvestButton(
            container,
            embeds,
            errors,
            withdraw=True,
            currency=currency,
            available_text=withdraw_cap,
        ).callback,
    )
    return builder.build()
