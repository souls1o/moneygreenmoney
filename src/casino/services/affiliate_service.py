from __future__ import annotations

from decimal import Decimal

from motor.motor_asyncio import AsyncIOMotorClientSession

from casino.domain.affiliate import (
    AffiliateReferral,
    AffiliateStats,
    affiliate_cut_lamports,
    affiliate_rate_bps,
)
from casino.domain.enums import LedgerEntryType, TransactionType
from casino.domain.records import GameRecord, UserRecord, is_house_seat
from casino.errors import AffiliateError
from casino.logging.logger import get_logger
from casino.repositories.user_repository import UserRepository
from casino.services.balance_service import BalanceService
from casino.services.user_service import UserService
from casino.utils.clock import Clock
from casino.utils.money import SolAmount


class AffiliateService:
    """One immutable referrer per player. Pays a cut of collected house fee only."""

    def __init__(
        self,
        user_repository: UserRepository,
        user_service: UserService,
        balance_service: BalanceService,
        clock: Clock,
    ) -> None:
        self._users = user_repository
        self._user_service = user_service
        self._balances = balance_service
        self._clock = clock
        self._logger = get_logger("casino.affiliate")

    async def bind(self, user_id: str, referrer_id: str) -> UserRecord:
        if user_id == referrer_id:
            raise AffiliateError("You cannot refer yourself.")
        if is_house_seat(referrer_id) or is_house_seat(user_id):
            raise AffiliateError("That user cannot be used as a referrer.")
        await self._user_service.get_or_create(user_id)
        await self._user_service.get_or_create(referrer_id)
        existing = await self._users.find_by_discord_id(user_id)
        if existing is not None and existing.referred_by:
            raise AffiliateError("Your referrer is locked and cannot be changed.")
        bound = await self._users.bind_referrer(user_id, referrer_id, self._clock.now())
        if bound is None:
            current = await self._users.find_by_discord_id(user_id)
            if current is not None and current.referred_by:
                raise AffiliateError("Your referrer is locked and cannot be changed.")
            raise AffiliateError("Could not set your referrer.")
        self._logger.info("affiliate_bound", userId=user_id, referredBy=referrer_id)
        return bound

    async def stats(self, user_id: str) -> AffiliateStats:
        user = await self._user_service.get_or_create(user_id)
        referrals = await self._users.list_referred_by(user_id)
        return AffiliateStats(
            referred_by=user.referred_by,
            referred_at=user.referred_at,
            own_referred_wagered_usd=user.referred_wagered_usd,
            own_rate_bps=affiliate_rate_bps(user.referred_wagered_usd) if user.referred_by else 0,
            referral_count=len(referrals),
            earned_lamports=user.affiliate_earned_lamports,
            earned_usd=user.affiliate_earned_usd,
            referrals=[
                AffiliateReferral(
                    discord_id=referral.discord_id,
                    referred_wagered_usd=referral.referred_wagered_usd,
                    rate_bps=affiliate_rate_bps(referral.referred_wagered_usd),
                    referred_at=referral.referred_at,
                )
                for referral in referrals
            ],
        )

    async def settle_game(
        self,
        game: GameRecord,
        *,
        collected_fee: SolAmount,
        price_usd: Decimal,
        winners: list[str] | None = None,
        session: AsyncIOMotorClientSession | None = None,
    ) -> list[str]:
        if collected_fee.is_zero():
            return []
        human_winners = [
            player_id
            for player_id in (winners if winners is not None else game.winner_ids)
            if not is_house_seat(player_id)
        ]
        if not human_winners:
            return []
        payouts: dict[str, int] = {}
        for player_id in human_winners:
            user = await self._users.find_by_discord_id(player_id, session=session)
            if user is None or not user.referred_by or user.referred_by == player_id:
                continue
            if is_house_seat(user.referred_by):
                continue
            rate = affiliate_rate_bps(user.referred_wagered_usd)
            cut = affiliate_cut_lamports(collected_fee.lamports, rate, len(human_winners))
            if cut > 0:
                payouts[user.referred_by] = payouts.get(user.referred_by, 0) + cut
        ledger_ids: list[str] = []
        for affiliate_id, lamports in payouts.items():
            affiliate = await self._users.find_by_discord_id(affiliate_id, session=session)
            if affiliate is None:
                continue
            amount = SolAmount.from_lamports(lamports)
            _treasury, house_entry = await self._balances.debit_house_reserve(
                amount=amount,
                price_usd=price_usd,
                description=f"Affiliate payout {game.game_id}",
                game_id=game.game_id,
                entry_type=LedgerEntryType.AFFILIATE,
                transaction_type=TransactionType.AFFILIATE,
                session=session,
            )
            credit = await self._balances.credit(
                user_id=affiliate_id,
                amount=amount,
                entry_type=LedgerEntryType.AFFILIATE,
                transaction_type=TransactionType.AFFILIATE,
                price_usd=price_usd,
                description=f"Affiliate payout {game.game_id}",
                game_id=game.game_id,
                session=session,
            )
            earned_usd = amount.to_decimal() * price_usd
            await self._users.add_affiliate_earnings(
                affiliate_id,
                amount,
                earned_usd,
                self._clock.now(),
                session=session,
            )
            ledger_ids.append(house_entry.entry_id)
            ledger_ids.append(credit.ledger_entry.entry_id)
            self._logger.info(
                "affiliate_paid",
                affiliateId=affiliate_id,
                gameId=game.game_id,
                amountLamports=lamports,
            )
        return ledger_ids
