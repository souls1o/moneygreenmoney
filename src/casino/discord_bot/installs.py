from __future__ import annotations

from typing import Any


def is_home_guild(settings: Any, guild_id: int | str | None) -> bool:
    home = getattr(settings, "discord_guild_id", None)
    if home is None or guild_id is None or guild_id == "":
        return False
    return str(guild_id) == str(home)


def is_inline_game(settings: Any, game: Any) -> bool:
    """Roaming / user-app tables have no private channel and are not on the home guild."""
    if is_home_guild(settings, getattr(game, "guild_id", None)):
        return False
    return getattr(game, "private_channel_id", None) is None


def should_number_game(settings: Any, game: Any) -> bool:
    """House Coinflip # / Dice # is only for finished home-server tables."""
    return not is_inline_game(settings, game)
