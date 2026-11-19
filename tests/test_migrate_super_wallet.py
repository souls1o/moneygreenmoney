from __future__ import annotations

from casino.solana_client.rpc import RENT_EXEMPT_FALLBACK_LAMPORTS, SWEEP_FEE_LAMPORTS
from casino.tools.migrate_super_wallet import build_plan


def test_plan_splits_players_then_house_and_counts_fees() -> None:
    plan = build_plan(
        super_address="Super111",
        house_address="House111",
        super_balance=10_000_000_000,
        rent=RENT_EXEMPT_FALLBACK_LAMPORTS,
        fee=SWEEP_FEE_LAMPORTS,
        house_book=3_000_000_000,
        players=[("1", "Depo1", 1_000_000_000), ("2", "Depo2", 2_000_000_000)],
    )
    assert [leg.kind for leg in plan.legs] == ["player", "player", "house"]
    assert plan.player_total == 3_000_000_000
    assert plan.needed == 6_000_000_000 + 3 * SWEEP_FEE_LAMPORTS
    assert plan.blockers == ()


def test_plan_blocks_when_super_is_short_or_tables_are_open() -> None:
    short = build_plan(
        super_address="Super111",
        house_address="House111",
        super_balance=1_000_000,
        rent=RENT_EXEMPT_FALLBACK_LAMPORTS,
        fee=SWEEP_FEE_LAMPORTS,
        house_book=5_000_000_000,
        players=[],
    )
    assert short.blockers
    open_tables = build_plan(
        super_address="Super111",
        house_address="House111",
        super_balance=10_000_000_000,
        rent=RENT_EXEMPT_FALLBACK_LAMPORTS,
        fee=SWEEP_FEE_LAMPORTS,
        house_book=1_000_000_000,
        players=[],
        open_tables=2,
    )
    assert any("open games" in reason for reason in open_tables.blockers)
    forced = build_plan(
        super_address="Super111",
        house_address="House111",
        super_balance=10_000_000_000,
        rent=RENT_EXEMPT_FALLBACK_LAMPORTS,
        fee=SWEEP_FEE_LAMPORTS,
        house_book=1_000_000_000,
        players=[],
        open_tables=2,
        force=True,
    )
    assert forced.blockers == ()
