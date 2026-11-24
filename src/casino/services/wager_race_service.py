from __future__ import annotations

from decimal import Decimal

from pymongo.errors import DuplicateKeyError

from casino.db.unit_of_work import UnitOfWorkFactory
from casino.domain.enums import GameType, LedgerEntryType, TransactionType, WagerRaceStatus
from casino.domain.records import HOUSE_USER_ID, WagerRaceRecord
from casino.domain.wager_race import (
    prize_usd_list,
    prizes_to_lamports,
    qualifying_lamports,
    race_id_for,
    race_week_end,
    race_week_start,
)
from casino.errors import InsufficientHouseReserveError, NotFoundError
from casino.logging.logger import get_logger
from casino.repositories.house_repository import HouseRepository
from casino.repositories.wager_race_repository import WagerRaceRepository
from casino.services.balance_service import BalanceService
from casino.services.guild_config_service import GuildConfigService
from casino.services.price_service import PriceService
from casino.utils.clock import Clock
from casino.utils.money import SolAmount


class WagerRaceService:
    def __init__(
        self,
        races: WagerRaceRepository,
        guild_configs: GuildConfigService,
        balances: BalanceService,
        house: HouseRepository,
        price_service: PriceService,
        clock: Clock,
        unit_of_work: UnitOfWorkFactory,
    ) -> None:
        self._races = races
        self._configs = guild_configs
        self._balances = balances
        self._house = house
        self._prices = price_service
        self._clock = clock
        self._uow = unit_of_work
        self._logger = get_logger("casino.wager_race")

    async def get(self, race_id: str) -> WagerRaceRecord | None:
        return await self._races.find_by_id(race_id)

    async def live_for_guild(self, guild_id: str) -> WagerRaceRecord | None:
        return await self._races.find_open(guild_id)

    async def attach_listing(self, race_id: str, channel_id: str, message_id: str) -> None:
        await self._races.set_listing_ref(race_id, channel_id, message_id)

    async def record_wager(
        self,
        guild_id: str | None,
        user_id: str,
        wager_lamports: int,
        *,
        game_type: GameType,
        limbo_target: Decimal | None = None,
        session=None,
    ) -> WagerRaceRecord | None:
        if not guild_id or not user_id or user_id == HOUSE_USER_ID:
            return None
        counted = qualifying_lamports(
            wager_lamports, game_type=game_type, limbo_target=limbo_target
        )
        if counted <= 0:
            return None
        live = await self._races.find_open(guild_id, session=session)
        if live is None:
            return None
        now = self._clock.now()
        if live.ends_at is not None and live.ends_at <= now:
            return None
        return await self._races.add_wager(live.race_id, user_id, counted, session=session)

    async def ensure_open(
        self,
        guild_id: str,
        *,
        channel_id: str | None = None,
        message_id: str | None = None,
    ) -> WagerRaceRecord:
        now = self._clock.now()
        starts = race_week_start(now)
        live = await self._races.find_open(guild_id)
        if live is not None and live.starts_at == starts:
            return live
        if live is not None and live.ends_at is not None and live.ends_at <= now:
            await self.settle(live.race_id)
        race_id = race_id_for(guild_id, starts)
        existing = await self._races.find_by_id(race_id)
        if existing is not None:
            return existing
        config = await self._configs.get(guild_id)
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        prizes_usd = prize_usd_list(
            config.wager_race_first_usd,
            config.wager_race_second_usd,
            config.wager_race_third_usd,
            config.wager_race_fourth_fifth_usd,
        )
        race = WagerRaceRecord(
            race_id=race_id,
            guild_id=guild_id,
            status=WagerRaceStatus.OPEN,
            starts_at=starts,
            ends_at=race_week_end(starts),
            prize_usd=[str(value) for value in prizes_usd],
            prize_lamports=prizes_to_lamports(prizes_usd, usd_price),
            channel_id=channel_id or config.wager_race_channel_id,
            message_id=message_id or config.wager_race_message_id,
            created_at=now,
        )
        try:
            await self._races.insert(race)
        except DuplicateKeyError:
            found = await self._races.find_by_id(race_id)
            if found is not None:
                return found
            raise
        self._logger.info("wager_race_opened", raceId=race.race_id, guildId=guild_id, endsAt=str(race.ends_at))
        return race

    async def settle_due(self) -> list[WagerRaceRecord]:
        settled: list[WagerRaceRecord] = []
        for race in await self._races.list_due(self._clock.now()):
            try:
                settled.append(await self.settle(race.race_id))
            except Exception:
                self._logger.exception("wager_race_settle_failed", raceId=race.race_id)
        return settled

    async def ensure_configured(self) -> list[WagerRaceRecord]:
        opened: list[WagerRaceRecord] = []
        for guild_id in await self._configs.list_wager_race_guild_ids():
            try:
                opened.append(await self.ensure_open(guild_id))
            except Exception:
                self._logger.exception("wager_race_ensure_failed", guildId=guild_id)
        return opened

    async def settle(self, race_id: str) -> WagerRaceRecord:
        quote = await self._prices.get_quote(allow_stale=True)
        usd_price = quote.sol_price("USD")
        async with self._uow.begin() as uow:
            race = await self._races.find_by_id(race_id, session=uow.session)
            if race is None:
                raise NotFoundError("That wager race was not found.")
            if race.status is WagerRaceStatus.COMPLETED:
                return race
            now = self._clock.now()
            race.status = WagerRaceStatus.SETTLING
            ranked = race.ranked()
            race.place_ids = [user_id for user_id, _wager in ranked]
            treasury = await self._house.get(session=uow.session)
            available = treasury.sol_balance_lamports
            for place, (user_id, _wager) in enumerate(ranked, start=1):
                prize = min(race.prize_for_place(place), available)
                if prize <= 0:
                    continue
                amount = SolAmount.from_lamports(prize)
                try:
                    _book, debit = await self._balances.debit_house_reserve(
                        amount=amount,
                        price_usd=usd_price,
                        description=f"Wager race {race.race_id} place {place}",
                        game_id=race.race_id,
                        entry_type=LedgerEntryType.WAGER_RACE,
                        transaction_type=TransactionType.WAGER_RACE,
                        session=uow.session,
                    )
                except InsufficientHouseReserveError:
                    break
                race.ledger_ids.append(debit.entry_id)
                credit = await self._balances.credit(
                    user_id=user_id,
                    amount=amount,
                    entry_type=LedgerEntryType.WAGER_RACE,
                    transaction_type=TransactionType.WAGER_RACE,
                    price_usd=usd_price,
                    description=f"Wager race place {place} {race.race_id}",
                    game_id=race.race_id,
                    session=uow.session,
                )
                race.ledger_ids.append(credit.ledger_entry.entry_id)
                race.payouts_lamports[user_id] = prize
                available -= prize
            race.status = WagerRaceStatus.COMPLETED
            race.settled_at = now
            replaced = await self._races.replace(race, session=uow.session)
            if not replaced:
                current = await self._races.find_by_id(race_id, session=uow.session)
                if current is not None:
                    return current
        self._logger.info(
            "wager_race_settled",
            raceId=race.race_id,
            placeIds=race.place_ids,
            payouts=race.payouts_lamports,
        )
        return race
