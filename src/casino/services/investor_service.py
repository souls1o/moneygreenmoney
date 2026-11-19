from __future__ import annotations

from decimal import Decimal

from motor.motor_asyncio import AsyncIOMotorClientSession

from casino.config.settings import Settings
from casino.db.unit_of_work import UnitOfWorkFactory
from casino.domain.enums import LedgerEntryType, TransactionType
from casino.domain.investor import (
    InvestorPosition,
    equity_lamports,
    forecast_ev_lamports,
    ownership_bps,
    redeem,
    shares_to_burn,
    shares_to_mint,
)
from casino.domain.records import HouseTreasuryRecord, UserRecord, is_house_seat
from casino.errors import InvestorError, WithdrawalError
from casino.logging.logger import get_logger
from casino.repositories.house_repository import HouseRepository
from casino.repositories.user_repository import UserRepository
from casino.services.balance_service import BalanceService
from casino.services.custody_service import CustodyService
from casino.services.house_stats_service import HouseStatsService
from casino.services.price_service import PriceService
from casino.services.user_service import UserService
from casino.services.validation_service import ValidationService
from casino.utils.clock import Clock
from casino.utils.money import MathService, SolAmount


class InvestorService:
    """Unitized NAV of the house book. Fees and house-game PnL move the book; shares stay put."""

    def __init__(
        self,
        user_service: UserService,
        user_repository: UserRepository,
        balance_service: BalanceService,
        house_repository: HouseRepository,
        price_service: PriceService,
        validation: ValidationService,
        math: MathService,
        clock: Clock,
        unit_of_work: UnitOfWorkFactory,
        settings: Settings,
        house_stats: HouseStatsService | None = None,
        custody: CustodyService | None = None,
    ) -> None:
        self._users = user_service
        self._user_repo = user_repository
        self._balances = balance_service
        self._house = house_repository
        self._prices = price_service
        self._validation = validation
        self._math = math
        self._clock = clock
        self._uow = unit_of_work
        self._settings = settings
        self._house_stats = house_stats
        self._custody = custody or CustodyService()
        self._logger = get_logger("casino.investor")

    async def position(self, user_id: str) -> InvestorPosition:
        user = await self._users.get_or_create(user_id)
        async with self._uow.begin() as uow:
            treasury = await self.ensure_capitalized(session=uow.session)
        handle, ev = await self._forecast()
        return self._position_for(user, treasury, human_handle_lamports=handle, house_ev_lamports=ev)

    async def deposit(self, user_id: str, amount: SolAmount) -> InvestorPosition:
        if is_house_seat(user_id):
            raise InvestorError("The house cannot invest in itself.")
        if amount.is_zero():
            raise InvestorError("Enter an amount greater than zero.")
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        self._validation.validate_invest(self._math.sol_to_fiat(amount, usd_price))
        await self._users.get_or_create(user_id)
        async with self._uow.begin() as uow:
            treasury = await self.ensure_capitalized(session=uow.session)
            book = treasury.sol_balance_lamports
            pool = treasury.pool_shares()
            if book <= 0 or pool <= 0:
                raise InvestorError("The house book is empty. An admin must fund it before investors can buy in.")
            minted = shares_to_mint(amount.lamports, book, pool)
            if minted <= 0:
                raise InvestorError("That amount is too small to mint house shares at the current NAV.")
            await self._balances.debit(
                user_id=user_id,
                amount=amount,
                entry_type=LedgerEntryType.INVEST,
                transaction_type=TransactionType.INVEST,
                price_usd=usd_price,
                description="Invest in house book",
                session=uow.session,
            )
            await self._balances.credit_house_reserve(
                amount=amount,
                entry_type=LedgerEntryType.INVEST,
                transaction_type=TransactionType.INVEST,
                price_usd=usd_price,
                description=f"Investor deposit from {user_id}",
                session=uow.session,
            )
            await self._house.adjust_shares(investor_delta=minted, now=self._clock.now(), session=uow.session)
            user = await self._user_repo.add_investor_shares(
                user_id, minted, self._clock.now(), session=uow.session
            )
            treasury = await self._house.get(session=uow.session)
        await self._custody.player_to_house(user_id, amount)
        handle, ev = await self._forecast()
        self._logger.info(
            "investor_deposit",
            userId=user_id,
            amountLamports=amount.lamports,
            sharesMinted=minted,
            userShares=user.investor_shares,
        )
        return self._position_for(user, treasury, human_handle_lamports=handle, house_ev_lamports=ev)

    async def withdraw(self, user_id: str, amount: SolAmount) -> InvestorPosition:
        if is_house_seat(user_id):
            raise InvestorError("The house cannot divest this way.")
        if amount.is_zero():
            raise InvestorError("Enter an amount greater than zero.")
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        await self._users.get_or_create(user_id)
        async with self._uow.begin() as uow:
            treasury = await self.ensure_capitalized(session=uow.session)
            user = await self._user_repo.find_by_discord_id(user_id, session=uow.session)
            if user is None:
                raise InvestorError("You do not have an investor stake.")
            burned, paid = redeem(
                amount.lamports,
                treasury.sol_balance_lamports,
                treasury.pool_shares(),
                user.investor_shares,
            )
            if burned <= 0 or paid <= 0:
                raise InvestorError("You do not have enough house shares, or the house book has no free cash.")
            payout = SolAmount.from_lamports(paid)
            await self._balances.debit_house_reserve(
                amount=payout,
                price_usd=usd_price,
                description=f"Investor withdrawal to {user_id}",
                entry_type=LedgerEntryType.DIVEST,
                transaction_type=TransactionType.DIVEST,
                session=uow.session,
            )
            await self._balances.credit(
                user_id=user_id,
                amount=payout,
                entry_type=LedgerEntryType.DIVEST,
                transaction_type=TransactionType.DIVEST,
                price_usd=usd_price,
                description="Withdraw investor stake to balance",
                session=uow.session,
            )
            await self._house.adjust_shares(investor_delta=-burned, now=self._clock.now(), session=uow.session)
            user = await self._user_repo.remove_investor_shares(
                user_id, burned, self._clock.now(), session=uow.session
            )
            treasury = await self._house.get(session=uow.session)
        await self._custody.house_to_player(user_id, payout)
        handle, ev = await self._forecast()
        self._logger.info(
            "investor_withdraw",
            userId=user_id,
            amountLamports=paid,
            sharesBurned=burned,
            userShares=user.investor_shares,
        )
        return self._position_for(user, treasury, human_handle_lamports=handle, house_ev_lamports=ev)

    async def ensure_capitalized(
        self, session: AsyncIOMotorClientSession | None = None
    ) -> HouseTreasuryRecord:
        treasury = await self._house.get(session=session)
        if treasury.pool_shares() == 0 and treasury.sol_balance_lamports > 0:
            return await self._house.seed_founder_shares(
                treasury.sol_balance_lamports, self._clock.now(), session=session
            )
        return treasury

    async def on_founder_inflow(
        self,
        amount: SolAmount,
        *,
        book_before: int,
        session: AsyncIOMotorClientSession | None = None,
    ) -> HouseTreasuryRecord:
        if amount.is_zero():
            return await self._house.get(session=session)
        treasury = await self._house.get(session=session)
        pool = treasury.pool_shares()
        if pool <= 0:
            if treasury.sol_balance_lamports <= 0:
                return treasury
            return await self._house.seed_founder_shares(
                treasury.sol_balance_lamports, self._clock.now(), session=session
            )
        minted = shares_to_mint(amount.lamports, book_before, pool)
        if minted <= 0:
            return treasury
        return await self._house.adjust_shares(
            founder_delta=minted, now=self._clock.now(), session=session
        )

    async def founder_outflow_shares(
        self,
        amount: SolAmount,
        session: AsyncIOMotorClientSession | None = None,
    ) -> int:
        treasury = await self.ensure_capitalized(session=session)
        burned = shares_to_burn(
            amount.lamports,
            treasury.sol_balance_lamports,
            treasury.pool_shares(),
            treasury.founder_shares,
        )
        if burned is None:
            cap = SolAmount.from_lamports(
                equity_lamports(
                    treasury.founder_shares,
                    treasury.sol_balance_lamports,
                    treasury.pool_shares(),
                )
            )
            raise WithdrawalError(
                "That would take investor capital from the house book. "
                f"Founder can withdraw up to `{self._math.format_sol(cap)} SOL`."
            )
        return burned

    async def apply_founder_share_delta(
        self,
        shares: int,
        session: AsyncIOMotorClientSession | None = None,
    ) -> None:
        if shares == 0:
            return
        await self._house.adjust_shares(founder_delta=shares, now=self._clock.now(), session=session)

    def _position_for(
        self,
        user: UserRecord,
        treasury: HouseTreasuryRecord,
        *,
        human_handle_lamports: int,
        house_ev_lamports: int,
    ) -> InvestorPosition:
        pool = treasury.pool_shares()
        user_equity = equity_lamports(user.investor_shares, treasury.sol_balance_lamports, pool)
        founder_equity = equity_lamports(treasury.founder_shares, treasury.sol_balance_lamports, pool)
        investor_pool = equity_lamports(treasury.investor_shares, treasury.sol_balance_lamports, pool)
        user_bps = ownership_bps(user.investor_shares, pool)
        user_ev = (house_ev_lamports * user.investor_shares) // pool if pool else 0
        return InvestorPosition(
            book_lamports=treasury.sol_balance_lamports,
            founder_shares=treasury.founder_shares,
            investor_shares=treasury.investor_shares,
            user_shares=user.investor_shares,
            user_equity_lamports=user_equity,
            founder_equity_lamports=founder_equity,
            investor_pool_lamports=investor_pool,
            ownership_bps=user_bps,
            human_handle_lamports=human_handle_lamports,
            forecast_ev_lamports=house_ev_lamports,
            user_forecast_ev_lamports=user_ev,
            house_fee_bps=self._math.house_fee_bps,
            player_balance_lamports=user.sol_balance_lamports,
            min_invest_usd=Decimal(self._settings.min_invest_usd),
            max_invest_usd=Decimal(self._settings.max_invest_usd),
        )

    async def _forecast(self) -> tuple[int, int]:
        if self._house_stats is None:
            return 0, 0
        report = await self._house_stats.snapshot()
        all_time = report.timeframes[-1] if report.timeframes else None
        if all_time is None:
            return 0, 0
        handle = max(0, all_time.volume_lamports - all_time.house_wagered_lamports)
        return handle, forecast_ev_lamports(handle, self._math.house_fee_bps)
