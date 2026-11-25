from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

from solders.pubkey import Pubkey

from casino.utils.money import SolAmount

MEMO_PROGRAM_IDS = {
    "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr",
    "Memo1UhkJRfHyvLMcVucJwxXeuD728EqVDDwQDxFMNo",
}
SYSTEM_PROGRAM_ID = "11111111111111111111111111111111"
SYSTEM_TRANSFER_TYPES = {"transfer", "transferWithSeed", "createAccount", "createAccountWithSeed"}
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
SYSTEM_TRANSFER = 2
SYSTEM_TRANSFER_WITH_SEED = 11
SYSTEM_CREATE_ACCOUNT = 0
SYSTEM_CREATE_ACCOUNT_WITH_SEED = 3


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
        destination = _norm_pubkey(destination) or destination
        account_keys = self._message_account_keys(message, meta)
        instructions = self._all_instructions(message, meta)
        from_ix = self._from_system_instructions(instructions, account_keys, destination, signature)
        dest_index = _index_of(account_keys, destination)
        pre = _int_list(meta.get("preBalances") or meta.get("pre_balances"))
        post = _int_list(meta.get("postBalances") or meta.get("post_balances"))
        if dest_index is not None and dest_index < len(pre) and dest_index < len(post):
            delta = int(post[dest_index]) - int(pre[dest_index])
            if delta > 0:
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
            if dest_index < len(pre) and dest_index < len(post):
                return None
            raise IncompleteDepositTransaction(signature)
        return None

    def _message_account_keys(self, message: dict[str, Any], meta: dict[str, Any]) -> list[str]:
        keys = self._account_keys(
            message.get("accountKeys")
            or message.get("account_keys")
            or message.get("staticAccountKeys")
            or message.get("static_account_keys")
            or []
        )
        loaded = meta.get("loadedAddresses") or meta.get("loaded_addresses") or {}
        if isinstance(loaded, dict):
            keys.extend(self._account_keys(loaded.get("writable") or []))
            keys.extend(self._account_keys(loaded.get("readonly") or []))
        return [_norm_pubkey(key) or key for key in keys if key]

    def _from_system_instructions(
        self,
        instructions: list[Any],
        account_keys: list[str],
        destination: str,
        signature: str,
    ) -> ParsedIncomingTransfer | None:
        total = 0
        source: str | None = None
        for instruction in instructions:
            if not isinstance(instruction, dict):
                continue
            parsed_ix = self._parsed_system_transfer(instruction, account_keys, destination)
            if parsed_ix is None:
                continue
            amount, src = parsed_ix
            total += amount
            source = src or source
        if total <= 0:
            return None
        return ParsedIncomingTransfer(
            signature=signature,
            lamports=total,
            destination=destination,
            source=source,
            memo=self._extract_memo(instructions),
        )

    def _parsed_system_transfer(
        self,
        instruction: dict[str, Any],
        account_keys: list[str],
        destination: str,
    ) -> tuple[int, str | None] | None:
        parsed = instruction.get("parsed")
        if isinstance(parsed, dict):
            kind = str(parsed.get("type") or "")
            if kind not in SYSTEM_TRANSFER_TYPES:
                return None
            info = parsed.get("info")
            if not isinstance(info, dict):
                return None
            dest = _norm_pubkey(
                str(info.get("destination") or info.get("newAccount") or info.get("new_account") or "")
            )
            if dest != destination:
                return None
            lamports = _int(info.get("lamports"))
            if lamports <= 0:
                return None
            return lamports, _norm_pubkey(str(info.get("source") or "")) or None

        program_id = _instruction_program_id(instruction, account_keys)
        if program_id != SYSTEM_PROGRAM_ID:
            return None
        accounts = self._instruction_accounts(instruction, account_keys)
        data = _instruction_data_bytes(instruction.get("data"))
        decoded = _decode_system_lamports(data)
        if decoded is None or not accounts:
            return None
        kind, lamports = decoded
        if lamports <= 0:
            return None
        dest = accounts[1] if kind in {SYSTEM_TRANSFER, SYSTEM_TRANSFER_WITH_SEED} and len(accounts) > 1 else None
        if dest is None and kind in {SYSTEM_CREATE_ACCOUNT, SYSTEM_CREATE_ACCOUNT_WITH_SEED} and len(accounts) > 1:
            dest = accounts[1]
        if dest != destination:
            return None
        src = accounts[0] if accounts else None
        return lamports, src

    def _instruction_accounts(self, instruction: dict[str, Any], account_keys: list[str]) -> list[str]:
        raw = instruction.get("accounts") or instruction.get("accountKeys") or []
        if not isinstance(raw, list):
            return []
        accounts: list[str] = []
        for item in raw:
            if isinstance(item, int):
                if 0 <= item < len(account_keys):
                    accounts.append(account_keys[item])
                continue
            if isinstance(item, str) and item.isdigit() and int(item) < len(account_keys):
                accounts.append(account_keys[int(item)])
                continue
            key = self._account_keys([item])
            if key:
                accounts.append(_norm_pubkey(key[0]) or key[0])
        return accounts

    def _all_instructions(self, message: dict[str, Any], meta: dict[str, Any]) -> list[Any]:
        instructions: list[Any] = []
        outer = message.get("instructions") or message.get("compiledInstructions") or []
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
                parsed.append(_norm_pubkey(key) or key)
            elif isinstance(key, dict):
                parsed.append(_norm_pubkey(str(key.get("pubkey") or key.get("pubkeyString") or "")) or "")
        return [key for key in parsed if key]

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


def _instruction_program_id(instruction: dict[str, Any], account_keys: list[str]) -> str:
    raw = instruction.get("programId") or instruction.get("program_id")
    if isinstance(raw, str) and not raw.isdigit():
        return _norm_pubkey(raw) or raw
    index = instruction.get("programIdIndex")
    if index is None:
        index = instruction.get("program_id_index")
    if index is None and isinstance(raw, str) and raw.isdigit():
        index = int(raw)
    if isinstance(index, int) and 0 <= index < len(account_keys):
        return account_keys[index]
    if instruction.get("program") == "system":
        return SYSTEM_PROGRAM_ID
    return _norm_pubkey(str(raw or "")) or ""


def _instruction_data_bytes(data: object) -> bytes:
    if data is None:
        return b""
    if isinstance(data, bytes):
        return data
    if isinstance(data, list):
        try:
            return bytes(int(item) & 0xFF for item in data)
        except (TypeError, ValueError):
            return b""
    text = str(data).strip()
    if not text:
        return b""
    decoded = _b58decode(text)
    if decoded:
        return decoded
    try:
        import base64

        return base64.b64decode(text, validate=False)
    except Exception:
        return b""


def _decode_system_lamports(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4:
        return None
    kind = struct.unpack_from("<I", data, 0)[0]
    if kind in {SYSTEM_TRANSFER, SYSTEM_TRANSFER_WITH_SEED} and len(data) >= 12:
        return kind, int(struct.unpack_from("<Q", data, 4)[0])
    if kind in {SYSTEM_CREATE_ACCOUNT, SYSTEM_CREATE_ACCOUNT_WITH_SEED} and len(data) >= 12:
        return kind, int(struct.unpack_from("<Q", data, 4)[0])
    return None


def _b58decode(text: str) -> bytes:
    n = 0
    try:
        for char in text:
            n = n * 58 + _B58.index(char)
    except ValueError:
        return b""
    pad = 0
    for char in text:
        if char != "1":
            break
        pad += 1
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * pad + body


def _norm_pubkey(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    try:
        return str(Pubkey.from_string(text))
    except Exception:
        return text


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
    for index, key in enumerate(keys):
        if key == destination:
            return index
    return None


def _int_list(values: object) -> list[int]:
    if not isinstance(values, list):
        return []
    return [_int(value) for value in values]


def _int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
