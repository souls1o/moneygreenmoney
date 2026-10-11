from __future__ import annotations

from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed application configuration. Never hard-code secrets or fees."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    discord_token: SecretStr
    discord_guild_id: int | None = None
    developer_user_id: int | None = None
    admin_role_ids: str = ""

    mongodb_uri: SecretStr
    mongodb_database: str = "betbetbet"

    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    solana_commitment: str = "confirmed"
    solana_private_key: SecretStr | None = None
    super_wallet_private_key: SecretStr | None = None
    deposit_master_secret: SecretStr | None = None

    coingecko_base_url: str = "https://api.coingecko.com/api/v3"
    price_cache_ttl_seconds: int = Field(default=45, ge=5, le=300)

    house_fee_bps: int = Field(default=300, ge=0, le=10_000)
    min_bet_usd: str = "0.01"
    min_rain_usd: str = "50"
    min_rain_seconds: int = Field(default=10, ge=1)
    max_rain_seconds: int = Field(default=604_800, ge=10)
    max_call_bot_usd: str = "1000"
    house_dice_roll_delay_seconds: float = Field(default=2, ge=0, le=30)
    game_ttl_seconds: int = Field(default=300, ge=30)
    auto_flip_after_seconds: int = Field(default=3600, ge=30)
    table_idle_delete_seconds: int = Field(default=300, ge=30)
    deposit_poll_interval_seconds: int = Field(default=15, ge=5)
    expiry_poll_interval_seconds: int = Field(default=20, ge=5)
    rate_limit_commands_per_window: int = Field(default=8, ge=1)
    rate_limit_window_seconds: int = Field(default=10, ge=1)

    enable_withdrawals: bool = False
    enable_deposit_monitor: bool = True

    log_level: str = "INFO"
    log_json: bool = False
    environment: str = "development"
    supported_currencies: str = "USD,EUR,GBP,CAD,AUD"
    default_currency: str = "USD"

    provably_fair_verifier_url: str = "https://rtpspy.com/provably-fair-verifier"

    @field_validator(
        "solana_private_key",
        "super_wallet_private_key",
        "deposit_master_secret",
        mode="before",
    )
    @classmethod
    def blank_secret_to_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, SecretStr):
            return value if value.get_secret_value().strip() else None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("developer_user_id", mode="before")
    @classmethod
    def blank_developer_to_none(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("default_currency")
    @classmethod
    def normalize_default_currency(cls, value: str) -> str:
        return value.strip().upper()

    @property
    def has_solana_wallet(self) -> bool:
        return self.super_wallet_private_key is not None and self.deposit_master_secret is not None

    @model_validator(mode="after")
    def validate_currency_support(self) -> Self:
        allowed = self.currency_codes()
        if self.default_currency not in allowed:
            raise ValueError(
                f"DEFAULT_CURRENCY {self.default_currency} is not in SUPPORTED_CURRENCIES"
            )
        return self

    def currency_codes(self) -> tuple[str, ...]:
        return tuple(
            code.strip().upper()
            for code in self.supported_currencies.split(",")
            if code.strip()
        )

    def admin_role_id_set(self) -> frozenset[int]:
        return self.parse_id_set(self.admin_role_ids)

    @staticmethod
    def parse_id_set(raw: str) -> frozenset[int]:
        if not raw.strip():
            return frozenset()
        return frozenset(int(part.strip()) for part in raw.split(",") if part.strip())

    def verify_page_url(self) -> str:
        return self.provably_fair_verifier_url.strip().rstrip("/")

    @property
    def house_fee_rate_display(self) -> str:
        whole = self.house_fee_bps // 100
        frac = self.house_fee_bps % 100
        if frac == 0:
            return f"{whole}%"
        return f"{self.house_fee_bps / 100:.2f}%"
