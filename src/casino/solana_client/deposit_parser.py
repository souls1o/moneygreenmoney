from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from casino.utils.money import SolAmount

MEMO_PROGRAM_IDS = {
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",
    "Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo",
}


@dataclass(frozen=True, slots=True)
class ParsedIncomingTransfer:
    signature: str
    lamports: int
    destination: str
    source: str | None
    memo: str | None

    @property
    def amount(self) -> SolAmount:
        return SolAmount.from_lamports(self.lamports)


class SolanaDepositParser:
    """Extract native SOL transfers into a watched deposit address."""

    def parse(
        self,
        payload: dict[str, Any],
        *,
        destination: str,
        signature: str,
    ) -> ParsedIncomingTransfer | None:
        meta = payload.get("meta") or {}
        if meta.get("err"):
            return None
        transaction = payload.get("transaction") or {}
        message = transaction.get("message") or {}
        account_keys = self._account_keys(message.get("accountKeys") or [])
        try:
            dest_index = account_keys.index(destination)
        except ValueError:
            return None

        pre = meta.get("preBalances") or []
        post = meta.get("postBalances") or []
        if dest_index >= len(pre) or dest_index >= len(post):
            return None
        delta = int(post[dest_index]) - int(pre[dest_index])
        if delta <= 0:
            return None

        memo = self._extract_memo(message.get("instructions") or [])
        source = self._first_other_account(account_keys, destination)
        return ParsedIncomingTransfer(
            signature=signature,
            lamports=delta,
            destination=destination,
            source=source,
            memo=memo,
        )

    def _account_keys(self, keys: list[Any]) -> list[str]:
        parsed: list[str] = []
        for key in keys:
            if isinstance(key, str):
                parsed.append(key)
            elif isinstance(key, dict):
                parsed.append(str(key.get("pubkey", "")))
        return parsed

    def _extract_memo(self, instructions: list[Any]) -> str | None:
        for instruction in instructions:
            if not isinstance(instruction, dict):
                continue
            program_id = str(instruction.get("programId") or instruction.get("programIdIndex") or "")
            parsed = instruction.get("parsed")
            if isinstance(parsed, str) and (
                instruction.get("program") == "spl-memo" or program_id in MEMO_PROGRAM_IDS
            ):
                return parsed.strip()
            info = parsed.get("info") if isinstance(parsed, dict) else None
            if isinstance(info, dict) and "memo" in info:
                return str(info["memo"]).strip()
            if instruction.get("program") == "spl-memo" and isinstance(parsed, str):
                return parsed.strip()
        return None

    def _first_other_account(self, keys: list[str], destination: str) -> str | None:
        for key in keys:
            if key and key != destination:
                return key
        return None
