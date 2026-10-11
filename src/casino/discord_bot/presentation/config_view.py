from __future__ import annotations

import discord

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.domain.enums import GuildConfigSlot
from casino.domain.records import GuildConfigRecord


class _ConfigChannelSelect(discord.ui.ChannelSelect):
    def __init__(
        self,
        container: AppContainer,
        embeds: EmbedFactory,
        slot: GuildConfigSlot,
        *,
        placeholder: str,
        channel_types: list[discord.ChannelType],
        page: int,
    ) -> None:
        super().__init__(
            placeholder=placeholder,
            channel_types=channel_types,
            min_values=1,
            max_values=1,
        )
        self._container = container
        self._embeds = embeds
        self._slot = slot
        self._page = page

    async def callback(self, interaction: discord.Interaction) -> None:
        self._container.permissions.require_admin(
            interaction.user.id,
            [role.id for role in interaction.user.roles] if isinstance(interaction.user, discord.Member) else [],
        )
        if interaction.guild_id is None:
            await interaction.response.send_message(
                **self._embeds.error("Use this in a server.").send_kwargs(ephemeral=True)
            )
            return
        values = self.values
        if not values:
            await interaction.response.send_message(
                **self._embeds.error("Select a channel.").send_kwargs(ephemeral=True)
            )
            return
        config = await self._container.guild_configs.set_slot(
            str(interaction.guild_id), self._slot, str(values[0].id)
        )
        panel = build_admin_config(self._container, self._embeds, config, page=self._page)
        await interaction.response.send_message(**panel.send_kwargs(ephemeral=True))


class _ConfigRoleSelect(discord.ui.RoleSelect):
    def __init__(
        self,
        container: AppContainer,
        embeds: EmbedFactory,
        slot: GuildConfigSlot,
        *,
        placeholder: str,
        page: int,
    ) -> None:
        super().__init__(
            placeholder=placeholder,
            min_values=0,
            max_values=1,
        )
        self._container = container
        self._embeds = embeds
        self._slot = slot
        self._page = page

    async def callback(self, interaction: discord.Interaction) -> None:
        self._container.permissions.require_admin(
            interaction.user.id,
            [role.id for role in interaction.user.roles] if isinstance(interaction.user, discord.Member) else [],
        )
        if interaction.guild_id is None:
            await interaction.response.send_message(
                **self._embeds.error("Use this in a server.").send_kwargs(ephemeral=True)
            )
            return
        role_id = str(self.values[0].id) if self.values else None
        config = await self._container.guild_configs.set_slot(
            str(interaction.guild_id), self._slot, role_id
        )
        panel = build_admin_config(self._container, self._embeds, config, page=self._page)
        await interaction.response.send_message(**panel.send_kwargs(ephemeral=True))


def build_admin_config(
    container: AppContainer,
    embeds: EmbedFactory,
    config: GuildConfigRecord,
    *,
    page: int = 0,
):
    builder = embeds.guild_config(config)
    text = [discord.ChannelType.text]
    category = [discord.ChannelType.category]
    if page == 0:
        builder.add_item(
            _ConfigChannelSelect(
                container,
                embeds,
                GuildConfigSlot.OPEN_COINFLIP,
                placeholder="Open coinflips channel",
                channel_types=text,
                page=0,
            )
        )
        builder.add_item(
            _ConfigChannelSelect(
                container,
                embeds,
                GuildConfigSlot.COINFLIP_OUTCOMES,
                placeholder="Coinflip outcomes channel",
                channel_types=text,
                page=0,
            )
        )
        builder.add_item(
            _ConfigChannelSelect(
                container,
                embeds,
                GuildConfigSlot.COINFLIP_CATEGORY,
                placeholder="Coinflip games category",
                channel_types=category,
                page=0,
            )
        )
        builder.add_item(
            _ConfigChannelSelect(
                container,
                embeds,
                GuildConfigSlot.PROVABLY_FAIR,
                placeholder="Provably fair channel",
                channel_types=text,
                page=0,
            )
        )
        builder.button(
            "Dice channels",
            style=discord.ButtonStyle.secondary,
            callback=_page_callback(container, embeds, 1),
        )
        builder.button(
            "Admin alerts",
            style=discord.ButtonStyle.secondary,
            callback=_page_callback(container, embeds, 2),
        )
    elif page == 1:
        builder.add_item(
            _ConfigChannelSelect(
                container,
                embeds,
                GuildConfigSlot.OPEN_DICE,
                placeholder="Open dice channel",
                channel_types=text,
                page=1,
            )
        )
        builder.add_item(
            _ConfigChannelSelect(
                container,
                embeds,
                GuildConfigSlot.DICE_OUTCOMES,
                placeholder="Dice outcomes channel",
                channel_types=text,
                page=1,
            )
        )
        builder.add_item(
            _ConfigChannelSelect(
                container,
                embeds,
                GuildConfigSlot.DICE_CATEGORY,
                placeholder="Dice games category",
                channel_types=category,
                page=1,
            )
        )
        builder.add_item(
            _ConfigChannelSelect(
                container,
                embeds,
                GuildConfigSlot.RAIN,
                placeholder="Rain channel",
                channel_types=text,
                page=1,
            )
        )
        builder.add_item(
            _ConfigRoleSelect(
                container,
                embeds,
                GuildConfigSlot.RAIN_PING,
                placeholder="Rain ping (optional)",
                page=1,
            )
        )
        builder.button(
            "Admin alerts",
            style=discord.ButtonStyle.secondary,
            callback=_page_callback(container, embeds, 2),
        )
        builder.button(
            "Back",
            style=discord.ButtonStyle.secondary,
            callback=_page_callback(container, embeds, 0),
        )
    elif page == 2:
        builder.add_item(
            _ConfigChannelSelect(
                container,
                embeds,
                GuildConfigSlot.DEPOSIT_ALERTS,
                placeholder="Deposit alerts channel",
                channel_types=text,
                page=2,
            )
        )
        builder.add_item(
            _ConfigChannelSelect(
                container,
                embeds,
                GuildConfigSlot.WITHDRAWAL_ALERTS,
                placeholder="Withdrawal alerts channel",
                channel_types=text,
                page=2,
            )
        )
        builder.add_item(
            _ConfigChannelSelect(
                container,
                embeds,
                GuildConfigSlot.HOUSE_LEDGER,
                placeholder="House ledger channel",
                channel_types=text,
                page=2,
            )
        )
        builder.button(
            "Verify",
            style=discord.ButtonStyle.secondary,
            callback=_page_callback(container, embeds, 3),
        )
        builder.button(
            "Back",
            style=discord.ButtonStyle.secondary,
            callback=_page_callback(container, embeds, 1),
        )
    else:
        builder.add_item(
            _ConfigChannelSelect(
                container,
                embeds,
                GuildConfigSlot.VERIFY,
                placeholder="Verify channel",
                channel_types=text,
                page=3,
            )
        )
        builder.add_item(
            _ConfigRoleSelect(
                container,
                embeds,
                GuildConfigSlot.MEMBER_ROLE,
                placeholder="Member role",
                page=3,
            )
        )
        builder.button(
            "Back",
            style=discord.ButtonStyle.secondary,
            callback=_page_callback(container, embeds, 2),
        )
    return builder.build()


def _page_callback(container: AppContainer, embeds: EmbedFactory, page: int):
    async def callback(interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                **embeds.error("Use this in a server.").send_kwargs(ephemeral=True)
            )
            return
        config = await container.guild_configs.get(str(interaction.guild_id))
        panel = build_admin_config(container, embeds, config, page=page)
        await interaction.response.edit_message(**panel.edit_kwargs())

    return callback
