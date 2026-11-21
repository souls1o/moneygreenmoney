from __future__ import annotations

import discord

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.discord_bot.presentation.errors import DiscordErrorPresenter
from casino.discord_bot.presentation.modals import WalletModal
from casino.domain.records import UserRecord


class CurrencySelect(discord.ui.Select):
    def __init__(self, container: AppContainer, embeds: EmbedFactory, currencies: tuple[str, ...], current: str) -> None:
        options = [
            discord.SelectOption(label=code, value=code, default=code == current)
            for code in currencies
        ]
        super().__init__(
            placeholder="Display / betting currency",
            options=options,
            min_values=1,
            max_values=1,
        )
        self._container = container
        self._embeds = embeds

    async def callback(self, interaction: discord.Interaction) -> None:
        user = await self._container.settings_service.update_currency(
            str(interaction.user.id), str(self.values[0])
        )
        await interaction.response.send_message(
            **self._embeds.success("Currency updated", f"Bets will use **{user.display_currency}**.").send_kwargs(
                ephemeral=True
            )
        )


class RandomizeClientSeedButton(discord.ui.Button):
    def __init__(self, container: AppContainer, embeds: EmbedFactory, errors: DiscordErrorPresenter) -> None:
        super().__init__(label="Randomize client seed", style=discord.ButtonStyle.primary)
        self._container = container
        self._embeds = embeds
        self._errors = errors

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            user = await self._container.users.randomize_client_seed(str(interaction.user.id))
        except Exception as exc:
            await interaction.response.send_message(
                **self._errors.panel_for(exc).send_kwargs(ephemeral=True)
            )
            return
        await interaction.response.send_message(
            **self._embeds.success(
                "Client seed updated",
                (
                    f"New client seed `{user.fair_client_seed}`.\n"
                    f"Next server seed hash is still `{user.next_server_seed_hash}` "
                    f"(nonce `{user.fair_nonce}`)."
                ),
            ).send_kwargs(ephemeral=True)
        )


class WalletButton(discord.ui.Button):
    def __init__(self, container: AppContainer, embeds: EmbedFactory, errors: DiscordErrorPresenter) -> None:
        super().__init__(label="Set wallet", style=discord.ButtonStyle.secondary)
        self._container = container
        self._embeds = embeds
        self._errors = errors

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(WalletModal(self._container, self._embeds, self._errors))


def build_settings_panel(
    container: AppContainer,
    embeds: EmbedFactory,
    errors: DiscordErrorPresenter,
    user: UserRecord,
    currencies: tuple[str, ...],
):
    builder = embeds.settings(user, currencies)
    builder.add_item(CurrencySelect(container, embeds, currencies, user.display_currency))
    builder.button(
        "Randomize client seed",
        style=discord.ButtonStyle.primary,
        callback=RandomizeClientSeedButton(container, embeds, errors).callback,
    )
    builder.button(
        "Set wallet",
        style=discord.ButtonStyle.secondary,
        callback=WalletButton(container, embeds, errors).callback,
    )
    return builder.build()
