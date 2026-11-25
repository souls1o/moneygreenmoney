from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from casino.utils.money import SolAmount

MEMO_PROGRAM_IDS = {
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",
    "Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo",
}
SYSTEM_TRANSFER_TYPES = {"transfer", "transferWithSeed", "createAccount", "createAccountWithSeed"}


class IncompleteDepositTransaction(Exception):
    """RPC returned a transaction that mentions the address but has no usable balances yet."""


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
        payload = _unwrap_payload(payload)
        meta = _meta(payload)
        if meta.get("err"):
            return None
        transaction = payload.get("transaction")
        if not isinstance(transaction, dict):
            transaction = {}
        message = transaction.get("message")
        if not isinstance(message, dict):
            message = {}
        account_keys = self._account_keys(message.get("accountKeys") or [])
        loaded = meta.get("loadedAddresses") or meta.get("loaded_addresses") or {}
        if isinstance(loaded, dict):
            account_keys.extend(self._account_keys(loaded.get("writable") or []))
            account_keys.extend(self._account_keys(loaded.get("readonly") or []))
        instructions = self._all_instructions(message, meta)
        from_ix = self._from_system_instructions(instructions, destination, signature)
        dest_index = _index_of(account_keys, destination)
        pre = _int_list(meta.get("preBalances") or meta.get("pre_balances"))
        post = _int_list(meta.get("postBalances") or meta.get("post_balances"))
        if dest_index is not None and dest_index < len(pre) and dest_index < len(post):
            delta = int(post[dest_index]) - int(pre[dest_index])
            if delta <= 0:
                return None
            memo = self._extract_memo(instructions)
            source = self._source_account(account_keys, pre, post, destination)
            if from_ix is not None:
                source = from_ix.source or source
            return ParsedIncomingTransfer(
                signature=signature,
                lamports=delta,
                destination=destination,
                source=source,
                memo=memo,
            )
        if from_ix is not None:
            return from_ix
        if dest_index is not None:
            raise IncompleteDepositTransaction(signature)
        return None

    def _from_system_instructions(
        self,
        instructions: list[Any],
        destination: str,
        signature: str,
    ) -> ParsedIncomingTransfer | None:
        total = 0
        source: str | None = None
        for instruction in instructions:
            if not isinstance(instruction, dict):
                continue
            parsed = instruction.get("parsed")
            if not isinstance(parsed, dict):
                continue
            kind = str(parsed.get("type") or "")
            if kind not in SYSTEM_TRANSFER_TYPES:
                continue
            info = parsed.get("info")
            if not isinstance(info, dict):
                continue
            dest = str(info.get("destination") or info.get("newAccount") or info.get("new_account") or "")
            if dest != destination:
                continue
            lamports = int(info.get("lamports") or 0)
            if lamports <= 0:
                continue
            total += lamports
            source = str(info.get("source") or "") or source
        if total <= 0:
            return None
        return ParsedIncomingTransfer(
            signature=signature,
            lamports=total,
            destination=destination,
            source=source,
            memo=self._extract_memo(instructions),
        )

    def _all_instructions(self, message: dict[str, Any], meta: dict[str, Any]) -> list[Any]:
        instructions: list[Any] = []
        outer = message.get("instructions") or []
        if isinstance(outer, list):
            instructions.extend(outer)
        inner = meta.get("innerInstructions") or meta.get("inner_instructions") or []
        if isinstance(inner, list):
            for group in inner:
                if isinstance(group, dict):
                    nested = group.get("instructions") or []
                    if isinstance(nested, list):
                        instructions.extend(nested)
                elif isinstance(group, list):
                    instructions.extend(group)
        return instructions

    def _account_keys(self, keys: list[Any]) -> list[str]:
        parsed: list[str] = []
        for key in keys:
            if isinstance(key, str):
                parsed.append(key)
            elif isinstance(key, dict):
                parsed.append(str(key.get("pubkey") or ""))
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

    def _source_account(
        self,
        keys: list[str],
        pre: list[int],
        post: list[int],
        destination: str,
    ) -> str | None:
        best: str | None = None
        best_drop = 0
        for index, key in enumerate(keys):
            if not key or key == destination:
                continue
            if index >= len(pre) or index >= len(post):
                continue
            drop = int(pre[index]) - int(post[index])
            if drop > best_drop:
                best_drop = drop
                best = key
        if best:
            return best
        for key in keys:
            if key and key != destination:
                return key
        return None


def _unwrap_payload(payload: dict[str, Any]) -> dict[str, Any]:
    current = payload
    for _ in range(4):
        if not isinstance(current, dict):
            return payload
        meta = _meta(current)
        transaction = current.get("transaction")
        if meta and isinstance(transaction, dict) and "message" in transaction:
            return current
        if isinstance(transaction, dict) and _meta(transaction) and isinstance(transaction.get("transaction"), dict):
            current = transaction
            continue
        nested = current.get("result") or current.get("value")
        if isinstance(nested, dict):
            current = nested
            continue
        if isinstance(transaction, dict) and _meta(transaction):
            current = transaction
            continue
        break
    return current if isinstance(current, dict) else payload


def _meta(payload: dict[str, Any]) -> dict[str, Any]:
    meta = payload.get("meta")
    return meta if isinstance(meta, dict) else {}


def _index_of(keys: list[str], destination: str) -> int | None:
    try:
        return keys.index(destination)
    except ValueError:
        return None


def _int_list(values: object) -> list[int]:
    if not isinstance(values, list):
        return []
    numbers: list[int] = []
    for value in values:
        try:
            numbers.append(int(value))
        except (TypeError, ValueError):
            numbers.append(0)
    return numbers
