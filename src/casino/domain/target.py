from __future__ import annotations

from casino.errors import GameError

TARGET_MIN = 1
TARGET_MAX = 999


def parse_target_pick(value: object) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise GameError("Pick a whole number from 1 to 999.") from exc
    if parsed < TARGET_MIN or parsed > TARGET_MAX:
        raise GameError("Pick a whole number from 1 to 999.")
    return parsed


TARGET_DRAW_SECONDS = 5.0
TARGET_DRAW_FRAMES = 20


def target_draw_frames(target: int, *, max_frames: int = TARGET_DRAW_FRAMES) -> list[int]:
    """Linear climb from 1 to the draw, spread across a fixed number of frames."""
    goal = max(TARGET_MIN, min(TARGET_MAX, int(target)))
    count = max(1, min(max_frames, goal))
    if count == 1:
        return [goal]
    frames: list[int] = []
    for index in range(count):
        progress = index / (count - 1)
        value = round(1 + (goal - 1) * progress)
        if not frames or value > frames[-1]:
            frames.append(value)
    if frames[-1] != goal:
        frames.append(goal)
    return frames


def target_draw_interval(frame_count: int) -> float:
    if frame_count <= 1:
        return 0.0
    return TARGET_DRAW_SECONDS / (frame_count - 1)


def closest_winners(picks: dict[str, int], target: int) -> list[str]:
    if not picks:
        return []
    distances = {player_id: abs(int(pick) - target) for player_id, pick in picks.items()}
    best = min(distances.values())
    winners = [player_id for player_id, distance in distances.items() if distance == best]
    if len(winners) == len(picks):
        return []
    return winners
