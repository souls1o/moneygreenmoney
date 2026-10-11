from decimal import Decimal

from casino.utils.money import MathService, SolAmount


class TestMathService:
    def test_six_percent_of_ten_sol_pot(self) -> None:
        math = MathService(600)
        split = math.split_equal_pot(SolAmount.from_decimal(Decimal("5")))
        assert split.total_pot.lamports == 10_000_000_000
        assert split.house_fee.lamports == 600_000_000
        assert split.winner_payout.lamports == 9_400_000_000

    def test_usd_example_at_one_hundred_per_sol(self) -> None:
        math = MathService(600)
        bet = math.fiat_to_sol(Decimal("5"), Decimal("100"))
        split = math.split_equal_pot(bet)
        assert bet.lamports == 50_000_000
        assert split.house_fee.lamports == 6_000_000
        assert split.winner_payout.lamports == 94_000_000
        fee_usd = math.sol_to_fiat(split.house_fee, Decimal("100"))
        payout_usd = math.sol_to_fiat(split.winner_payout, Decimal("100"))
        assert fee_usd == Decimal("0.60")
        assert payout_usd == Decimal("9.40")

    def test_fee_uses_integer_lamports_not_floats(self) -> None:
        math = MathService(600)
        split = math.split_pot(SolAmount.from_lamports(1))
        assert split.house_fee.lamports == 0
        assert split.winner_payout.lamports == 1

    def test_configurable_fee_bps(self) -> None:
        math = MathService(300)
        split = math.split_equal_pot(SolAmount.from_decimal(Decimal("5")))
        assert split.house_fee.lamports == 300_000_000

    def test_team_pot_is_four_bets(self) -> None:
        math = MathService(600)
        split = math.split_matched_pot(SolAmount.from_lamports(50_000_000), 2)
        assert split.total_pot.lamports == 200_000_000
        assert split.house_fee.lamports == 12_000_000
        assert split.winner_payout.lamports == 188_000_000

    def test_split_evenly_keeps_remainder(self) -> None:
        math = MathService(600)
        shares = math.split_evenly(SolAmount.from_lamports(188_000_000), 2)
        assert [share.lamports for share in shares] == [94_000_000, 94_000_000]
        leftover = math.split_evenly(SolAmount.from_lamports(5), 2)
        assert sum(share.lamports for share in leftover) == 5

    def test_collected_fee_is_only_the_human_winners_share(self) -> None:
        math = MathService(600)
        pot_fee = SolAmount.from_lamports(24_000_000)
        assert math.collected_house_fee(pot_fee, human_winner_count=2, winner_count=2).lamports == 24_000_000
        assert math.collected_house_fee(pot_fee, human_winner_count=1, winner_count=2).lamports == 12_000_000
        assert math.collected_house_fee(pot_fee, human_winner_count=0, winner_count=2).lamports == 0

    def test_format_fiat_uses_currency_sign(self) -> None:
        math = MathService(600)
        assert math.format_fiat_symbol(Decimal("10"), "USD") == "$10.00"
        assert math.format_fiat_symbol(Decimal("10"), "EUR") == "€10.00"


def test_admin_ledger_amounts_use_signed_usd() -> None:
    from types import SimpleNamespace

    from casino.discord_bot.presentation.embeds import EmbedFactory

    factory = EmbedFactory(SimpleNamespace(math=MathService(600)))  # type: ignore[arg-type]
    assert factory._signed_usd(Decimal("1.20")) == "+$1.20"
    assert factory._signed_usd(Decimal("-4.50")) == "-$4.50"
    assert factory._signed_usd(Decimal("0")) == "+$0.00"
