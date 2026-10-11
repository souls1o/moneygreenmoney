from decimal import Decimal

from casino.domain.enums import UserRole
from casino.domain.ranks import RankCatalog
from casino.domain.records import UserRecord
from casino.services.rank_service import RankService


class TestRankService:
    def test_special_and_progression_badges(self) -> None:
        service = RankService(RankCatalog())
        user = UserRecord(
            discord_id="1",
            max_bet_usd=Decimal("500"),
            total_wagered_usd=Decimal("1200"),
            roles=[UserRole.FOUNDER, UserRole.PLAYER],
        )
        badges = service.badges_for(user)
        assert badges.founder is True
        codes = {rank.code for rank in badges.special_ranks}
        assert codes == {"100v100", "250v250", "500v500"}
        assert badges.progression.code == "sharp"
        assert RankCatalog.FOUNDER_EMOJI in badges.prefix_emojis()
