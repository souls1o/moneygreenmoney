from types import SimpleNamespace

from casino.solana_client.rpc import (
    RENT_EXEMPT_FALLBACK_LAMPORTS,
    SWEEP_FEE_LAMPORTS,
    house_align_transfer,
    signature_entries,
    sweepable_lamports,
)


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


def test_house_align_sends_wins_from_super_and_losses_to_super() -> None:
    rent = RENT_EXEMPT_FALLBACK_LAMPORTS
    fee = SWEEP_FEE_LAMPORTS
    book = 5_000_000_000
    assert house_align_transfer(house_balance=book, book_lamports=book, rent=rent) is None
    assert house_align_transfer(
        house_balance=book,
        book_lamports=book + 1_000_000_000,
        rent=rent,
    ) == ("to_house", 1_000_000_000)
    assert house_align_transfer(
        house_balance=book + 1_000_000_000,
        book_lamports=book,
        rent=rent,
        fee=fee,
    ) == ("to_super", 1_000_000_000 - fee)


def test_house_align_keeps_rent_when_book_is_empty() -> None:
    rent = RENT_EXEMPT_FALLBACK_LAMPORTS
    assert house_align_transfer(house_balance=rent, book_lamports=0, rent=rent) is None
    assert house_align_transfer(house_balance=0, book_lamports=0, rent=rent) == ("to_house", rent)
