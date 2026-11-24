from __future__ import annotations

from casino.config.settings import Settings
from casino.domain.enums import UserRole
from casino.domain.records import UserRecord
from casino.logging.logger import get_logger
from casino.repositories.user_repository import UserRepository
from casino.solana_client.wallets import DepositWalletDeriver
from casino.utils.clock import Clock
from casino.utils.identifiers import IdentifierFactory
from casino.utils.rng import FairPlaySeeds, ProvablyFairService


class UserService:
    def __init__(
        self,
        user_repository: UserRepository,
        identifiers: IdentifierFactory,
        clock: Clock,
        settings: Settings,
        deposit_wallets: DepositWalletDeriver | None = None,
        rng: ProvablyFairService | None = None,
    ) -> None:
        self._users = user_repository
        self._identifiers = identifiers
        self._clock = clock
        self._settings = settings
        self._deposit_wallets = deposit_wallets
        self._rng = rng or ProvablyFairService()
        self._logger = get_logger("casino.user")
        self._on_deposit_wallet: object | None = None

    def set_on_deposit_wallet(self, callback) -> None:
        self._on_deposit_wallet = callback

    def _notify_deposit_wallet(self) -> None:
        callback = self._on_deposit_wallet
        if callback is None:
            return
        callback()

    async def get(self, discord_id: str) -> UserRecord | None:
        return await self._users.find_by_discord_id(discord_id)

    async def get_by_memo(self, memo: str) -> UserRecord | None:
        return await self._users.find_by_deposit_memo(memo)

    async def get_by_deposit_address(self, address: str) -> UserRecord | None:
        return await self._users.find_by_deposit_address(address)

    async def list_deposit_wallets(self) -> list[tuple[str, str]]:
        return await self._users.list_deposit_wallets()

    async def get_or_create(self, discord_id: str) -> UserRecord:
        existing = await self._users.find_by_discord_id(discord_id)
        if existing is not None:
            existing = await self._ensure_deposit_address(existing)
            return await self.ensure_fairness(existing.discord_id)
        now = self._clock.now()
        user = UserRecord(
            discord_id=discord_id,
            deposit_memo=self._identifiers.deposit_memo(),
            deposit_address=self._deposit_address_for(discord_id),
            display_currency=self._settings.default_currency,
            created_at=now,
            updated_at=now,
        )
        try:
            await self._users.insert(user)
            self._logger.info("user_created", userId=discord_id, depositAddress=user.deposit_address)
            if user.deposit_address:
                self._notify_deposit_wallet()
            return await self.ensure_fairness(discord_id)
        except Exception:
            raced = await self._users.find_by_discord_id(discord_id)
            if raced is not None:
                raced = await self._ensure_deposit_address(raced)
                return await self.ensure_fairness(raced.discord_id)
            raise

    def _deposit_address_for(self, discord_id: str) -> str | None:
        if self._deposit_wallets is None:
            return None
        return self._deposit_wallets.address_for(discord_id)

    async def _ensure_deposit_address(self, user: UserRecord) -> UserRecord:
        if user.deposit_address or self._deposit_wallets is None:
            return user
        address = self._deposit_wallets.address_for(user.discord_id)
        updated = await self._users.set_deposit_address(user.discord_id, address, self._clock.now())
        self._notify_deposit_wallet()
        return updated or user

    async def set_currency(self, discord_id: str, currency: str) -> UserRecord:
        user = await self.get_or_create(discord_id)
        updated = await self._users.set_currency(user.discord_id, currency, self._clock.now())
        assert updated is not None
        return updated

    async def ensure_fairness(self, discord_id: str, session=None) -> UserRecord:
        user = await self._users.find_by_discord_id(discord_id)
        if user is None:
            user = await self.get_or_create(discord_id)
        if user.fair_client_seed and user.next_server_seed and user.next_server_seed_hash:
            return user
        commitment = self._rng.commit()
        next_nonce = user.fair_nonce if user.next_server_seed_hash else self._rng.nonce()
        updated = await self._users.set_fairness(
            discord_id,
            client_seed=user.fair_client_seed or self._rng.client_seed(),
            next_server_seed=user.next_server_seed or commitment.server_seed,
            next_server_seed_hash=user.next_server_seed_hash or commitment.server_seed_hash,
            fair_nonce=next_nonce,
            now=self._clock.now(),
            session=session,
        )
        assert updated is not None
        return updated

    async def take_fair_play(self, discord_id: str, session=None) -> FairPlaySeeds:
        user = await self.ensure_fairness(discord_id, session=session)
        nxt = self._rng.commit()
        used = FairPlaySeeds(
            server_seed=user.next_server_seed,
            server_seed_hash=user.next_server_seed_hash,
            client_seed=user.fair_client_seed,
            nonce=user.fair_nonce,
            next_server_seed_hash=nxt.server_seed_hash,
        )
        updated = await self._users.set_fairness(
            discord_id,
            client_seed=user.fair_client_seed,
            next_server_seed=nxt.server_seed,
            next_server_seed_hash=nxt.server_seed_hash,
            fair_nonce=self._rng.nonce(),
            now=self._clock.now(),
            session=session,
        )
        if updated is None:
            raise RuntimeError("Could not rotate the next fairness seed.")
        return used

    async def randomize_client_seed(self, discord_id: str) -> UserRecord:
        user = await self.ensure_fairness(discord_id)
        updated = await self._users.set_fairness(
            discord_id,
            client_seed=self._rng.client_seed(),
            next_server_seed=user.next_server_seed,
            next_server_seed_hash=user.next_server_seed_hash,
            fair_nonce=user.fair_nonce,
            now=self._clock.now(),
        )
        assert updated is not None
        return updated

    async def set_wallet(self, discord_id: str, wallet: str) -> UserRecord:
        user = await self.get_or_create(discord_id)
        updated = await self._users.set_wallet(user.discord_id, wallet, self._clock.now())
        assert updated is not None
        return updated

    async def grant_role(self, discord_id: str, role: UserRole) -> UserRecord:
        user = await self.get_or_create(discord_id)
        updated = await self._users.add_role(user.discord_id, role, self._clock.now())
        assert updated is not None
        self._logger.info("role_granted", userId=discord_id, role=role.value)
        return updated
