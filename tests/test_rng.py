from casino.domain.enums import CoinSide
from casino.utils.rng import FairRandomService, ProvablyFairService


class TestFairRandomService:
    def test_only_returns_coin_sides(self) -> None:
        rng = FairRandomService()
        results = {rng.flip_coin() for _ in range(64)}
        assert results <= {CoinSide.HEADS, CoinSide.TAILS}

    def test_both_sides_appear_over_many_flips(self) -> None:
        rng = FairRandomService()
        results = {rng.flip_coin() for _ in range(200)}
        assert CoinSide.HEADS in results
        assert CoinSide.TAILS in results


class TestProvablyFairService:
    def test_commit_hash_matches_seed(self) -> None:
        fair = ProvablyFairService()
        commitment = fair.commit()
        assert fair.hash_server_seed(commitment.server_seed) == commitment.server_seed_hash

    def test_same_inputs_are_deterministic(self) -> None:
        fair = ProvablyFairService()
        first = fair.resolve_coin("seed", "client", 0)
        second = fair.resolve_coin("seed", "client", 0)
        assert first is second
        assert first in {CoinSide.HEADS, CoinSide.TAILS}

    def test_verifier_roll_under_fifty_is_heads(self) -> None:
        fair = ProvablyFairService()
        roll = fair.resolve_roll("seed", "client", 0)
        assert 0 <= roll.roll < 100
        assert roll.side is (CoinSide.HEADS if roll.roll < 50 else CoinSide.TAILS)
        assert fair.resolve_coin("seed", "client", 0) is roll.side

    def test_verify_accepts_matching_result(self) -> None:
        fair = ProvablyFairService()
        commitment = fair.commit()
        result = fair.resolve_coin(commitment.server_seed, "game-1", 0)
        assert fair.verify(
            server_seed=commitment.server_seed,
            server_seed_hash=commitment.server_seed_hash,
            client_seed="game-1",
            nonce=0,
            expected=result,
        )
        opposite = CoinSide.TAILS if result is CoinSide.HEADS else CoinSide.HEADS
        assert not fair.verify(
            server_seed=commitment.server_seed,
            server_seed_hash=commitment.server_seed_hash,
            client_seed="game-1",
            nonce=0,
            expected=opposite,
        )

    def test_client_seeds_are_random(self) -> None:
        fair = ProvablyFairService()
        seeds = {fair.client_seed() for _ in range(20)}
        assert len(seeds) == 20
        assert all(len(seed) == 32 for seed in seeds)

    def test_flip_index_uses_incremented_nonce(self) -> None:
        fair = ProvablyFairService()
        later = fair.resolve_roll("seed", "client", 5, 1)
        shifted = fair.resolve_roll("seed", "client", 6, 0)
        first = fair.resolve_roll("seed", "client", 5, 0)
        assert later.digest == shifted.digest
        assert later.digest != first.digest

    def test_dice_rolls_are_deterministic_d6(self) -> None:
        fair = ProvablyFairService()
        first = fair.resolve_dice("seed", "client", 3, 0)
        second = fair.resolve_dice("seed", "client", 3, 0)
        assert first == second
        assert 1 <= first.die_a <= 6
        assert 1 <= first.die_b <= 6
        other = fair.resolve_dice("seed", "client", 3, 1)
        assert other.digest != first.digest
