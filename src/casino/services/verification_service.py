from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from casino.utils.clock import Clock

VERIFY_CODE_TTL_SECONDS = 300


class VerificationService:
    """Short-lived 6-digit codes keyed by guild and user."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._codes: dict[str, tuple[str, datetime]] = {}

    def issue(self, guild_id: str, user_id: str) -> str:
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires = self._clock.now() + timedelta(seconds=VERIFY_CODE_TTL_SECONDS)
        self._codes[self._key(guild_id, user_id)] = (code, expires)
        return code

    def check(self, guild_id: str, user_id: str, submitted: str) -> bool:
        key = self._key(guild_id, user_id)
        entry = self._codes.get(key)
        if entry is None:
            return False
        code, expires = entry
        if self._clock.now() >= expires:
            self._codes.pop(key, None)
            return False
        if submitted.strip() != code:
            return False
        self._codes.pop(key, None)
        return True

    def _key(self, guild_id: str, user_id: str) -> str:
        return f"{guild_id}:{user_id}"
