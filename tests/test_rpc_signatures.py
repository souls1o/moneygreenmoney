from types import SimpleNamespace

from casino.solana_client.rpc import RENT_EXEMPT_FALLBACK_LAMPORTS, SWEEP_FEE_LAMPORTS, signature_entries, sweepable_lamports


def test_signature_entries_accepts_solders_style_value() -> None:
    items = [SimpleNamespace(signature="sig1"), SimpleNamespace(signature="sig2")]
    assert signature_entries(SimpleNamespace(value=items)) == items


def test_signature_entries_accepts_a_bare_list() -> None:
    items = ["sig1"]
    assert signature_entries(items) == items


def test_signature_entries_rejects_public_rpc_int() -> None:
    assert signature_entries(1) is None
    assert signature_entries(SimpleNamespace(value=429)) is None


def test_sweep_keeps_rent_and_fee_on_the_source() -> None:
    deposit = 2_000_000_000
    moved = sweepable_lamports(deposit, RENT_EXEMPT_FALLBACK_LAMPORTS)
    assert moved == deposit - RENT_EXEMPT_FALLBACK_LAMPORTS - SWEEP_FEE_LAMPORTS
    assert sweepable_lamports(SWEEP_FEE_LAMPORTS, RENT_EXEMPT_FALLBACK_LAMPORTS) == 0
