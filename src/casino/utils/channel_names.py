from __future__ import annotations


class GameChannelNamer:
    """Discord channel names like coinflip-1 using a per-guild incrementing table id."""

    def for_values(self, game_type: str, table_number: int) -> str:
        return f"{game_type}-{max(int(table_number), 1)}"

    def for_game(self, game) -> str:
        return self.for_values(game.game_type.value, game.table_number or 1)
