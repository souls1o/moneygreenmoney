from __future__ import annotations

from decimal import Decimal

import discord

from casino.container import AppContainer
from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.domain.records import GameRecord, UserRecord
from casino.logging.logger import get_logger
from casino.solana_client.deposit_parser import ParsedIncomingTransfer
from casino.utils.money import SolAmount


class AdminChannelPresenter:
    """Posts deposit, withdrawal, and house P&L alerts to configured admin channels."""

    def __init__(self, bot: discord.Client, container: AppContainer) -> None:
        self._bot = bot
        self._container = container
        self._embeds = EmbedFactory(container)
        self._logger = get_logger("casino.discord.admin_alerts")

    async def on_player_deposit(self, user: UserRecord, parsed: ParsedIncomingTransfer, usd_price: Decimal) -> None:
        amount_usd = self._container.math.sol_to_fiat(parsed.amount, usd_price)
        await self._post(
            "deposit_alerts_channel_id",
            self._embeds.deposit_alert(
                user_id=user.discord_id,
                amount=parsed.amount,
                amount_usd=amount_usd,
                signature=parsed.signature,
                is_house=False,
            ),
        )

    async def on_house_deposit(self, parsed: ParsedIncomingTransfer, usd_price: Decimal) -> None:
        amount_usd = self._container.math.sol_to_fiat(parsed.amount, usd_price)
        await self._post(
            "deposit_alerts_channel_id",
            self._embeds.deposit_alert(
                user_id=None,
                amount=parsed.amount,
                amount_usd=amount_usd,
                signature=parsed.signature,
                is_house=True,
            ),
        )

    async def on_withdrawal(self, user_id: str, amount: SolAmount, destination: str, signature: str) -> None:
        quote = await self._container.price.get_quote(allow_stale=True)
        amount_usd = self._container.math.sol_to_fiat(amount, quote.sol_price("USD"))
        await self._post(
            "withdrawal_alerts_channel_id",
            self._embeds.withdrawal_alert(
                user_id=user_id,
                amount=amount,
                amount_usd=amount_usd,
                destination=destination,
                signature=signature,
            ),
        )

    async def on_game_resolved(self, game: GameRecord) -> None:
        await self._post(
            "house_ledger_channel_id",
            self._embeds.house_ledger_alert(game),
            guild_id=game.guild_id,
        )

    async def _post(self, slot_attr: str, panel, *, guild_id: str | None = None) -> None:
        try:
            channel_id = await self._channel_id(slot_attr, guild_id=guild_id)
            if not channel_id:
                return
            channel = self._bot.get_channel(int(channel_id))
            if channel is None:
                channel = await self._bot.fetch_channel(int(channel_id))
            if not isinstance(channel, discord.abc.Messageable):
                return
            await channel.send(**panel.send_kwargs())
        except Exception:
            self._logger.exception("admin_alert_failed", slot=slot_attr)
        finally:
            panel.stop()

    async def _channel_id(self, slot_attr: str, *, guild_id: str | None) -> str | None:
        target = guild_id or (
            str(self._container.settings.discord_guild_id) if self._container.settings.discord_guild_id else None
        )
        if not target:
            return None
        config = await self._container.guild_configs.get(target)
        value = getattr(config, slot_attr, None)
        return str(value) if value else None
