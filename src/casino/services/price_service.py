from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

import httpx

from casino.config.settings import Settings
from casino.errors import PriceUnavailableError
from casino.logging.logger import get_logger
from casino.utils.clock import Clock
from casino.utils.money import PRICE_QUANTUM


@dataclass(frozen=True, slots=True)
class PriceQuote:
    prices: dict[str, Decimal]
    fetched_at: datetime
    stale: bool = False

    def sol_price(self, currency: str) -> Decimal:
        key = currency.lower()
        if key not in self.prices:
            raise PriceUnavailableError(f"No SOL price available for {currency}.")
        return self.prices[key]


class PriceService:
    """Single source of truth for SOL/fiat prices with TTL cache and stale fallback."""

    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._http = http_client
        self._logger = get_logger("casino.price")
        self._cache: PriceQuote | None = None

    def peek_cache(self) -> PriceQuote | None:
        return self._cache

    async def get_quote(self, *, allow_stale: bool = True) -> PriceQuote:
        now = self._clock.now()
        if self._cache and not self._is_expired(self._cache, now):
            return self._cache
        try:
            fresh = await self._fetch(now)
            self._cache = fresh
            self._logger.info(
                "price_refreshed",
                usd=str(fresh.sol_price("USD")),
                stale=False,
            )
            return fresh
        except Exception as exc:
            if allow_stale and self._cache is not None:
                stale = PriceQuote(
                    prices=self._cache.prices,
                    fetched_at=self._cache.fetched_at,
                    stale=True,
                )
                self._logger.warning("price_fetch_failed_using_cache", error=str(exc))
                return stale
            self._logger.exception("price_unavailable")
            raise PriceUnavailableError() from exc

    def _is_expired(self, quote: PriceQuote, now: datetime) -> bool:
        return now - quote.fetched_at >= timedelta(seconds=self._settings.price_cache_ttl_seconds)

    async def _fetch(self, now: datetime) -> PriceQuote:
        currencies = ",".join(code.lower() for code in self._settings.currency_codes())
        url = f"{self._settings.coingecko_base_url.rstrip('/')}/simple/price"
        response = await self._http.get(
            url,
            params={"ids": "solana", "vs_currencies": currencies},
            timeout=8.0,
        )
        response.raise_for_status()
        payload = response.json()
        raw_prices = payload.get("solana") or {}
        prices: dict[str, Decimal] = {}
        for code in self._settings.currency_codes():
            raw = raw_prices.get(code.lower())
            if raw is None:
                continue
            prices[code.lower()] = Decimal(str(raw)).quantize(PRICE_QUANTUM)
        if "usd" not in prices:
            raise PriceUnavailableError("CoinGecko did not return a USD price for SOL.")
        return PriceQuote(prices=prices, fetched_at=now, stale=False)
