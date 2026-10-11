from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import httpx

from casino.config.settings import Settings
from casino.db.indexes import IndexInstaller
from casino.db.mongo import MongoDatabase
from casino.db.unit_of_work import UnitOfWorkFactory
from casino.domain.ranks import RankCatalog
from casino.logging.logger import configure_logging, get_logger
from casino.repositories.cursor_repository import CursorRepository
from casino.repositories.game_repository import GameRepository
from casino.repositories.guild_config_repository import GuildConfigRepository
from casino.repositories.house_repository import HouseRepository
from casino.repositories.ledger_repository import LedgerRepository
from casino.repositories.lock_repository import LockRepository
from casino.repositories.mappers import DocumentMapper
from casino.repositories.rain_repository import RainRepository
from casino.repositories.rate_limit_repository import RateLimitRepository
from casino.repositories.transaction_repository import TransactionRepository
from casino.repositories.user_repository import UserRepository
from casino.services.admin_service import AdminService
from casino.services.affiliate_service import AffiliateService
from casino.services.balance_service import BalanceService
from casino.services.deposit_service import DepositService
from casino.services.game_service import GameService
from casino.services.guild_config_service import GuildConfigService
from casino.services.house_stats_service import HouseStatsService
from casino.services.ledger_service import LedgerService
from casino.services.permission_service import PermissionService
from casino.services.price_service import PriceService
from casino.services.rain_service import RainService
from casino.services.rank_service import RankService
from casino.services.rate_limit_service import RateLimitService
from casino.services.settings_service import SettingsService
from casino.services.tip_service import TipService
from casino.services.user_service import UserService
from casino.services.validation_service import ValidationService
from casino.services.verification_service import VerificationService
from casino.services.withdrawal_service import WithdrawalService
from casino.solana_client.addresses import SolanaAddressValidator
from casino.solana_client.deposit_parser import SolanaDepositParser
from casino.solana_client.keys import KeypairLoader, normalize_secret
from casino.solana_client.rpc import SolanaRpcGateway
from casino.solana_client.wallets import DepositWalletDeriver
from casino.utils.clock import SystemClock
from casino.utils.identifiers import IdentifierFactory
from casino.utils.money import MathService
from casino.utils.rng import ProvablyFairService
from casino.utils.text import TextNormalizer
from casino.workers.deposit_worker import DepositMonitorWorker
from casino.workers.expiry_worker import GameExpiryWorker
from casino.workers.locks import DistributedLockService
from casino.workers.rain_worker import RainExpiryWorker
from casino.workers.scheduler import WorkerScheduler


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    mongo: MongoDatabase
    http_client: httpx.AsyncClient
    solana: SolanaRpcGateway
    math: MathService
    price: PriceService
    users: UserService
    balances: BalanceService
    ledger: LedgerService
    affiliates: AffiliateService
    games: GameService
    deposits: DepositService
    withdrawals: WithdrawalService
    validation: ValidationService
    settings_service: SettingsService
    ranks: RankService
    permissions: PermissionService
    rate_limits: RateLimitService
    admin: AdminService
    house_stats: HouseStatsService
    guild_configs: GuildConfigService
    verification: VerificationService
    rains: RainService
    tips: TipService
    workers: WorkerScheduler
    deposit_worker: DepositMonitorWorker
    expiry_worker: GameExpiryWorker
    rain_worker: RainExpiryWorker
    house: HouseRepository
    fair: ProvablyFairService

    async def startup(self) -> None:
        logger = get_logger("casino.app")
        await self.mongo.connect()
        await IndexInstaller(self.mongo).install()
        await self.solana.connect()
        await self.price.get_quote(allow_stale=False)
        self.workers.start(
            "game-expiry",
            self.settings.expiry_poll_interval_seconds,
            self.expiry_worker.tick,
        )
        self.workers.start(
            "rain-expiry",
            self.settings.expiry_poll_interval_seconds,
            self.rain_worker.tick,
        )
        if self.settings.enable_deposit_monitor and self.solana.is_configured:
            self.workers.start(
                "deposit-monitor",
                self.settings.deposit_poll_interval_seconds,
                self.deposit_worker.tick,
            )
        logger.info(
            "application_started",
            environment=self.settings.environment,
            solanaWallet=self.solana.is_configured,
        )

    async def shutdown(self) -> None:
        logger = get_logger("casino.app")
        await self.workers.stop()
        await self.http_client.aclose()
        await self.solana.close()
        await self.mongo.close()
        logger.info("application_stopped")


class ContainerFactory:
    def build(self, settings: Settings) -> AppContainer:
        configure_logging(settings)
        clock = SystemClock()
        identifiers = IdentifierFactory()
        math = MathService(settings.house_fee_bps)
        mongo = MongoDatabase(settings)
        mapper = DocumentMapper()
        http_client = httpx.AsyncClient(headers={"Accept": "application/json"})
        solana = SolanaRpcGateway(settings, KeypairLoader())
        uow = UnitOfWorkFactory(mongo)
        deposit_wallets = None
        if settings.deposit_master_secret is not None:
            deposit_wallets = DepositWalletDeriver(
                normalize_secret(settings.deposit_master_secret.get_secret_value())
            )

        users_repo = UserRepository(mongo, mapper)
        games_repo = GameRepository(mongo, mapper)
        tx_repo = TransactionRepository(mongo, mapper)
        ledger_repo = LedgerRepository(mongo, mapper)
        house_repo = HouseRepository(mongo, mapper)
        guild_config_repo = GuildConfigRepository(mongo, mapper)
        cursor_repo = CursorRepository(mongo, mapper)
        lock_repo = LockRepository(mongo)
        rate_repo = RateLimitRepository(mongo)

        ledger = LedgerService(ledger_repo, identifiers, clock)
        balances = BalanceService(users_repo, tx_repo, house_repo, ledger, identifiers, clock)
        users = UserService(users_repo, identifiers, clock, settings, deposit_wallets)
        price = PriceService(settings, clock, http_client)
        validation = ValidationService(
            math,
            Decimal(settings.min_bet_usd),
            settings.currency_codes(),
        )
        fair = ProvablyFairService()
        affiliates = AffiliateService(users_repo, users, balances, clock)
        games = GameService(
            games_repo,
            users_repo,
            users,
            balances,
            price,
            validation,
            math,
            fair,
            identifiers,
            clock,
            uow,
            settings,
            house_repo,
            affiliates,
        )
        guild_configs = GuildConfigService(guild_config_repo, clock)
        verification = VerificationService(clock)
        deposits = DepositService(
            users,
            balances,
            price,
            tx_repo,
            solana,
            SolanaDepositParser(),
            uow,
            TextNormalizer(),
        )
        address_validator = SolanaAddressValidator()
        settings_service = SettingsService(users, validation, address_validator, settings)
        ranks = RankService(RankCatalog())
        admin = AdminService(
            users,
            balances,
            games,
            house_repo,
            price,
            uow,
            solana,
            address_validator,
            tx_repo,
        )
        house_stats = HouseStatsService(games_repo, house_repo, price, clock, solana)
        lock_service = DistributedLockService(lock_repo, clock, identifiers)
        workers = WorkerScheduler(lock_service)
        deposit_worker = DepositMonitorWorker(deposits, solana, cursor_repo, clock)
        expiry_worker = GameExpiryWorker(games)
        rains_repo = RainRepository(mongo, mapper)
        rains = RainService(
            rains_repo,
            users,
            balances,
            price,
            validation,
            math,
            fair,
            identifiers,
            clock,
            uow,
            settings,
            house_repo,
        )
        rain_worker = RainExpiryWorker(rains)
        tips = TipService(users, balances, price, uow)

        return AppContainer(
            settings=settings,
            mongo=mongo,
            http_client=http_client,
            solana=solana,
            math=math,
            price=price,
            users=users,
            balances=balances,
            ledger=ledger,
            affiliates=affiliates,
            games=games,
            deposits=deposits,
            withdrawals=WithdrawalService(
                settings,
                users,
                address_validator,
                balances,
                ledger,
                price,
                solana,
                tx_repo,
                uow,
            ),
            validation=validation,
            settings_service=settings_service,
            ranks=ranks,
            permissions=PermissionService(settings),
            rate_limits=RateLimitService(rate_repo, clock, settings),
            admin=admin,
            house_stats=house_stats,
            guild_configs=guild_configs,
            verification=verification,
            rains=rains,
            tips=tips,
            workers=workers,
            deposit_worker=deposit_worker,
            expiry_worker=expiry_worker,
            rain_worker=rain_worker,
            house=house_repo,
            fair=fair,
        )
