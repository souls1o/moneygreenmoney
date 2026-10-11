from __future__ import annotations

from dataclasses import dataclass

from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from casino.utils.money import SolAmount


@dataclass(frozen=True, slots=True)
class TransferLeg:
    destination: Pubkey
    amount: SolAmount


class SolanaTransferBuilder:
    """Builds a single versioned transaction with one or more SOL transfers (withdrawal-ready)."""

    def build(
        self,
        *,
        payer: Keypair,
        recent_blockhash: Hash,
        legs: list[TransferLeg],
    ) -> VersionedTransaction:
        instructions = [
            transfer(
                TransferParams(
                    from_pubkey=payer.pubkey(),
                    to_pubkey=leg.destination,
                    lamports=leg.amount.lamports,
                )
            )
            for leg in legs
        ]
        message = MessageV0.try_compile(
            payer=payer.pubkey(),
            instructions=instructions,
            address_lookup_table_accounts=[],
            recent_blockhash=recent_blockhash,
        )
        return VersionedTransaction(message, [payer])
