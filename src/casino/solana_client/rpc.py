from __future__ import annotations

import json
from typing import Any

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Commitment, Confirmed, Finalized, Processed
from solana.rpc.models import TxOpts
from solders.hash import Hash
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from casino.config.settings import Settings
from casino.logging.logger import get_logger
from casino.solana_client.keys import KeypairLoader, normalize_secret
from casino.solana_client.transfers import SolanaTransferBuilder, TransferLeg
from casino.solana_client.wallets import DepositWalletDeriver
from casino.utils.money import SolAmount

SWEEP_FEE_LAMPORTS = 5_000
RENT_EXEMPT_FALLBACK_LAMPORTS = 890_880


class SolanaRpcUnavailableError(RuntimeError):
    """RPC returned a rate-limit, HTML, or otherwise unusable payload."""


def signature_entries(response: object) -> list[Any] | None:
    """Unwrap solders/solana-py signature pages. Public RPC can return a bare int."""
    if isinstance(response, list):
        return response
    value = getattr(response, "value", None)
    if isinstance(value, list):
        return value
    return None


class SolanaTransactionCodec:
    def as_dict(self, value: object) -> dict[str, Any]:
        if hasattr(value, "to_json"):
            return json.loads(value.to_json())
        if isinstance(value, dict):
            return value
        raise TypeError("Unsupported Solana transaction payload")


class SolanaRpcGateway:
    """Reconnect-friendly Solana RPC wrapper around the official Python SDK."""

    def __init__(
        self,
        settings: Settings,
        keypair_loader: KeypairLoader,
        codec: SolanaTransactionCodec | None = None,
        transfers: SolanaTransferBuilder | None = None,
    ) -> None:
        self._settings = settings
        self._keypair_loader = keypair_loader
        self._codec = codec or SolanaTransactionCodec()
        self._transfers = transfers or SolanaTransferBuilder()
        self._client: AsyncClient | None = None
        self._house_keypair: Keypair | None = None
        self._super_keypair: Keypair | None = None
        self._deposit_wallets: DepositWalletDeriver | None = None
        self._logger = get_logger("casino.solana")

    @property
    def commitment(self) -> Commitment:
        mapping = {
            "processed": Processed,
            "confirmed": Confirmed,
            "finalized": Finalized,
        }
        return mapping.get(self._settings.solana_commitment.lower(), Confirmed)

    @property
    def is_configured(self) -> bool:
        return self._super_keypair is not None and self._deposit_wallets is not None

    @property
    def has_house_wallet(self) -> bool:
        return self._house_keypair is not None

    @property
    def deposit_wallets(self) -> DepositWalletDeriver | None:
        return self._deposit_wallets

    @property
    def house_address(self) -> str | None:
        if self._house_keypair is None:
            return None
        return str(self._house_keypair.pubkey())

    @property
    def super_address(self) -> str | None:
        if self._super_keypair is None:
            return None
        return str(self._super_keypair.pubkey())

    @property
    def keypair(self) -> Keypair:
        if self._super_keypair is not None:
            return self._super_keypair
        if self._house_keypair is None:
            raise RuntimeError("Solana keypair is not loaded.")
        return self._house_keypair

    @property
    def public_address(self) -> str:
        return str(self.keypair.pubkey())

    @property
    def pubkey(self) -> Pubkey:
        return self.keypair.pubkey()

    def player_keypair(self, discord_id: str) -> Keypair:
        if self._deposit_wallets is None:
            raise RuntimeError("Deposit master secret is not configured.")
        return self._deposit_wallets.keypair_for(discord_id)

    def house_keypair(self) -> Keypair:
        if self._house_keypair is None:
            raise RuntimeError("House wallet is not configured.")
        return self._house_keypair

    async def connect(self) -> None:
        house_secret = self._settings.solana_private_key
        super_secret = self._settings.super_wallet_private_key
        master_secret = self._settings.deposit_master_secret
        if house_secret is not None:
            self._house_keypair = self._keypair_loader.load(house_secret.get_secret_value())
        if super_secret is not None:
            self._super_keypair = self._keypair_loader.load(super_secret.get_secret_value())
        if master_secret is not None:
            self._deposit_wallets = DepositWalletDeriver(normalize_secret(master_secret.get_secret_value()))
        if self._house_keypair is None and self._super_keypair is None and self._deposit_wallets is None:
            self._logger.warning(
                "solana_wallet_unconfigured",
                detail=(
                    "Set DEPOSIT_MASTER_SECRET and SUPER_WALLET_PRIVATE_KEY for player deposits. "
                    "SOLANA_PRIVATE_KEY is only the house deposit address."
                ),
            )
            return
        self._client = AsyncClient(self._settings.solana_rpc_url, commitment=self.commitment)
        ready = await self._client.is_connected()
        self._logger.info(
            "solana_connected",
            rpc=self._settings.solana_rpc_url,
            houseAddress=self.house_address,
            superAddress=self.super_address,
            depositsConfigured=self.is_configured,
            connected=ready,
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            self._logger.info("solana_disconnected")

    @property
    def client(self) -> AsyncClient:
        if self._client is None:
            raise RuntimeError("Solana RPC client is not connected.")
        return self._client

    async def get_balance(self, address: str) -> int:
        response = await self.client.get_balance(Pubkey.from_string(address))
        return int(response.value)

    async def get_latest_blockhash(self) -> Hash:
        response = await self.client.get_latest_blockhash()
        return response.value.blockhash

    async def get_signatures(
        self,
        address: str | None = None,
        *,
        until: str | None = None,
        before: str | None = None,
        limit: int = 25,
    ) -> list[str]:
        target = address or self.house_address or self.super_address
        if not target:
            return []
        until_sig = Signature.from_string(until) if until else None
        before_sig = Signature.from_string(before) if before else None
        response = await self.client.get_signatures_for_address(
            Pubkey.from_string(target),
            until=until_sig,
            before=before_sig,
            limit=limit,
        )
        items = signature_entries(response)
        if items is None:
            raise SolanaRpcUnavailableError(
                f"Signature lookup returned {type(response).__name__} for {target}."
            )
        return [str(getattr(item, "signature", item)) for item in items]

    async def get_parsed_transaction(self, signature: str) -> dict[str, Any] | None:
        response = await self.client.get_transaction(
            Signature.from_string(signature),
            encoding="jsonParsed",
            max_supported_transaction_version=0,
        )
        if response.value is None:
            return None
        inner = getattr(response.value, "transaction", response.value)
        return self._codec.as_dict(inner)

    async def get_rent_exempt_lamports(self, data_len: int = 0) -> int:
        try:
            response = await self.client.get_minimum_balance_for_rent_exemption(data_len)
            return int(response.value)
        except Exception:
            self._logger.warning("rent_exempt_lookup_failed", fallback=RENT_EXEMPT_FALLBACK_LAMPORTS)
            return RENT_EXEMPT_FALLBACK_LAMPORTS

    async def send_transaction(self, transaction: VersionedTransaction) -> str:
        opts = TxOpts(skip_preflight=True, skip_confirmation=True, max_retries=3)
        response = await self.client.send_transaction(transaction, opts=opts)
        return str(response.value)

    async def sweep_to_super(self, source: Keypair) -> str | None:
        if self._super_keypair is None:
            return None
        if source.pubkey() == self._super_keypair.pubkey():
            return None
        balance = await self.get_balance(str(source.pubkey()))
        if balance <= SWEEP_FEE_LAMPORTS:
            return None
        amount = SolAmount.from_lamports(balance - SWEEP_FEE_LAMPORTS)
        transaction = self._transfers.build(
            payer=source,
            recent_blockhash=await self.get_latest_blockhash(),
            legs=[TransferLeg(destination=self._super_keypair.pubkey(), amount=amount)],
        )
        signature = await self.send_transaction(transaction)
        self._logger.info(
            "deposit_swept",
            source=str(source.pubkey()),
            destination=self.super_address,
            amountLamports=amount.lamports,
            txSignature=signature,
        )
        return signature

    async def send_from_super(self, destination: str, amount: SolAmount) -> str:
        if self._super_keypair is None:
            raise RuntimeError("Super wallet is not configured.")
        dest = Pubkey.from_string(destination)
        if dest == self._super_keypair.pubkey():
            raise ValueError("Cannot withdraw to the custody wallet.")
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                transaction = self._transfers.build(
                    payer=self._super_keypair,
                    recent_blockhash=await self.get_latest_blockhash(),
                    legs=[TransferLeg(destination=dest, amount=amount)],
                )
                signature = await self.send_transaction(transaction)
                self._logger.info(
                    "withdrawal_sent",
                    destination=destination,
                    amountLamports=amount.lamports,
                    txSignature=signature,
                    attempt=attempt + 1,
                )
                return signature
            except Exception as exc:
                last_error = exc
                self._logger.warning(
                    "withdrawal_send_retry",
                    attempt=attempt + 1,
                    error=str(exc),
                )
        assert last_error is not None
        raise last_error
