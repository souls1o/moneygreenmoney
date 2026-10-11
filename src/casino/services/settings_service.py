from __future__ import annotations

from casino.config.settings import Settings
from casino.domain.records import UserRecord
from casino.services.user_service import UserService
from casino.services.validation_service import ValidationService
from casino.solana_client.addresses import SolanaAddressValidator


class SettingsService:
    def __init__(
        self,
        user_service: UserService,
        validation: ValidationService,
        address_validator: SolanaAddressValidator,
        settings: Settings,
    ) -> None:
        self._users = user_service
        self._validation = validation
        self._addresses = address_validator
        self._settings = settings

    def supported_currencies(self) -> tuple[str, ...]:
        return self._settings.currency_codes()

    async def update_currency(self, discord_id: str, currency: str) -> UserRecord:
        code = self._validation.require_supported_currency(currency)
        return await self._users.set_currency(discord_id, code)

    async def update_wallet(self, discord_id: str, wallet: str) -> UserRecord:
        address = self._addresses.parse(wallet)
        return await self._users.set_wallet(discord_id, address)
