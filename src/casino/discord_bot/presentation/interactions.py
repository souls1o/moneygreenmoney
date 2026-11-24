from __future__ import annotations

import discord

from casino.discord_bot.presentation.builder import CasinoPanel


async def ack(interaction: discord.Interaction, *, ephemeral: bool = True) -> None:
    """Acknowledge the Discord interaction before any I/O so the 3s window is not missed."""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=ephemeral)


async def send_panel(
    interaction: discord.Interaction,
    panel: CasinoPanel,
    *,
    ephemeral: bool = True,
) -> None:
    kwargs = panel.send_kwargs(ephemeral=ephemeral)
    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
        return
    await interaction.response.send_message(**kwargs)
