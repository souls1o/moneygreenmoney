from __future__ import annotations

from dataclasses import dataclass

from casino.domain.enums import UserRole
from casino.domain.ranks import ProgressionRank, RankCatalog, SpecialRank
from casino.domain.records import UserRecord


@dataclass(frozen=True, slots=True)
class ProfileBadges:
    founder: bool
    moderator: bool
    special_ranks: tuple[SpecialRank, ...]
    progression: ProgressionRank

    def prefix_emojis(self) -> str:
        parts: list[str] = []
        if self.founder:
            parts.append(RankCatalog.FOUNDER_EMOJI)
        if self.moderator:
            parts.append(RankCatalog.MODERATOR_EMOJI)
        parts.extend(rank.emoji for rank in self.special_ranks)
        parts.append(self.progression.emoji)
        return " ".join(parts)


class RankService:
    def __init__(self, catalog: RankCatalog) -> None:
        self._catalog = catalog

    def badges_for(self, user: UserRecord) -> ProfileBadges:
        return ProfileBadges(
            founder=user.has_role(UserRole.FOUNDER),
            moderator=user.has_role(UserRole.MODERATOR),
            special_ranks=self._catalog.special_ranks_for(user.max_bet_usd),
            progression=self._catalog.progression_rank_for(user.total_wagered_usd),
        )
