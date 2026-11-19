from __future__ import annotations

from casino.errors import JackpotError

JACKPOT_SECONDS = 60
JACKPOT_REVEAL_SECONDS = 10
JACKPOT_JOIN_CAP = 10


def max_join_lamports(opener_lamports: int) -> int:
    return max(int(opener_lamports), 0) * JACKPOT_JOIN_CAP


def require_join_amount(opener_lamports: int | None, amount_lamports: int) -> None:
    if opener_lamports is None:
        return
    cap = max_join_lamports(opener_lamports)
    if amount_lamports > cap:
        raise JackpotError("You can join with at most 10× the first player's amount.")


def pick_weighted_winner(entries: dict[str, int], ticket: int) -> str:
    if not entries:
        raise JackpotError("Nobody joined this jackpot.")
    total = sum(max(int(value), 0) for value in entries.values())
    if total <= 0:
        raise JackpotError("Nobody joined this jackpot.")
    cursor = 0
    last = next(iter(entries))
    point = int(ticket) % total
    for player_id, lamports in entries.items():
        last = player_id
        cursor += max(int(lamports), 0)
        if point < cursor:
            return player_id
    return last


def win_chance(bet_lamports: int, pot_lamports: int) -> float:
    if pot_lamports <= 0:
        return 0.0
    return max(int(bet_lamports), 0) / pot_lamports
