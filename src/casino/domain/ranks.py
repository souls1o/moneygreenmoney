from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class SpecialRank:
    code: str
    emoji: str
    min_single_bet_usd: Decimal
    label: str


@dataclass(frozen=True, slots=True)
class ProgressionRank:
    code: str
    emoji: str
    min_wagered_usd: Decimal
    label: str


class RankCatalog:
    """Badge and rank thresholds expressed in USD notional of a single-side bet / lifetime wagered."""

    SPECIAL_RANKS: tuple[SpecialRank, ...] = (
        SpecialRank("1kv1k", "💎", Decimal("1000"), "1k v 1k"),
        SpecialRank("500v500", "🟣", Decimal("500"), "500 v 500"),
        SpecialRank("250v250", "🔵", Decimal("250"), "250 v 250"),
        SpecialRank("100v100", "🟢", Decimal("100"), "100 v 100"),
    )

    PROGRESSION_RANKS: tuple[ProgressionRank, ...] = (
        ProgressionRank("highroller", "👑", Decimal("10000"), "High Roller"),
        ProgressionRank("whale", "🐋", Decimal("2500"), "Whale"),
        ProgressionRank("sharp", "🎯", Decimal("500"), "Sharp"),
        ProgressionRank("regular", "⭐", Decimal("100"), "Regular"),
        ProgressionRank("grinder", "🪙", Decimal("25"), "Grinder"),
        ProgressionRank("newbie", "🌱", Decimal("0"), "Newbie"),
    )

    FOUNDER_EMOJI = "🏆"
    MODERATOR_EMOJI = "🛡️"

    def special_ranks_for(self, max_single_bet_usd: Decimal) -> tuple[SpecialRank, ...]:
        return tuple(rank for rank in self.SPECIAL_RANKS if max_single_bet_usd >= rank.min_single_bet_usd)

    def progression_rank_for(self, total_wagered_usd: Decimal) -> ProgressionRank:
        for rank in self.PROGRESSION_RANKS:
            if total_wagered_usd >= rank.min_wagered_usd:
                return rank
        return self.PROGRESSION_RANKS[-1]
