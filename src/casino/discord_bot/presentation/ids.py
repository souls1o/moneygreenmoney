from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HistoryTarget:
    user_id: str
    page: int


class ComponentIdCodec:
    JOIN = "cf:join:"
    JOIN_HEADS = "cf:join_heads:"
    JOIN_TAILS = "cf:join_tails:"
    READY = "cf:ready:"
    CALL_BOT = "cf:call:"
    LEAVE = "cf:leave:"
    RERUN = "cf:rerun:"
    ACCEPT = "cf:accept:"
    DECLINE = "cf:decline:"
    CANCEL = "cf:cancel:"
    DEPOSIT = "bal:deposit"
    WITHDRAW = "bal:withdraw"
    HISTORY = "profile:history:"
    CURRENCY = "settings:currency"
    PF_CHECK = "pf:check"
    PF_GAME = "pf:game:"
    RAIN_JOIN = "rain:join:"
    VERIFY_START = "verify:start"
    VERIFY_ENTER = "verify:enter"

    def join(self, game_id: str) -> str:
        return f"{self.JOIN}{game_id}"

    def join_heads(self, game_id: str) -> str:
        return f"{self.JOIN_HEADS}{game_id}"

    def join_tails(self, game_id: str) -> str:
        return f"{self.JOIN_TAILS}{game_id}"

    def ready(self, game_id: str) -> str:
        return f"{self.READY}{game_id}"

    def call_bot(self, game_id: str) -> str:
        return f"{self.CALL_BOT}{game_id}"

    def leave(self, game_id: str) -> str:
        return f"{self.LEAVE}{game_id}"

    def rerun(self, game_id: str) -> str:
        return f"{self.RERUN}{game_id}"

    def accept_rematch(self, game_id: str) -> str:
        return f"{self.ACCEPT}{game_id}"

    def decline_rematch(self, game_id: str) -> str:
        return f"{self.DECLINE}{game_id}"

    def cancel(self, game_id: str) -> str:
        return f"{self.CANCEL}{game_id}"

    def verify_game(self, game_id: str) -> str:
        return f"{self.PF_GAME}{game_id}"

    def rain_join(self, rain_id: str) -> str:
        return f"{self.RAIN_JOIN}{rain_id}"

    def history(self, user_id: str, page: int) -> str:
        return f"{self.HISTORY}{user_id}:{page}"

    def parse_join(self, custom_id: str) -> str | None:
        return custom_id[len(self.JOIN) :] if custom_id.startswith(self.JOIN) else None

    def parse_join_heads(self, custom_id: str) -> str | None:
        return custom_id[len(self.JOIN_HEADS) :] if custom_id.startswith(self.JOIN_HEADS) else None

    def parse_join_tails(self, custom_id: str) -> str | None:
        return custom_id[len(self.JOIN_TAILS) :] if custom_id.startswith(self.JOIN_TAILS) else None

    def parse_ready(self, custom_id: str) -> str | None:
        return custom_id[len(self.READY) :] if custom_id.startswith(self.READY) else None

    def parse_call_bot(self, custom_id: str) -> str | None:
        return custom_id[len(self.CALL_BOT) :] if custom_id.startswith(self.CALL_BOT) else None

    def parse_leave(self, custom_id: str) -> str | None:
        return custom_id[len(self.LEAVE) :] if custom_id.startswith(self.LEAVE) else None

    def parse_rerun(self, custom_id: str) -> str | None:
        return custom_id[len(self.RERUN) :] if custom_id.startswith(self.RERUN) else None

    def parse_accept(self, custom_id: str) -> str | None:
        return custom_id[len(self.ACCEPT) :] if custom_id.startswith(self.ACCEPT) else None

    def parse_decline(self, custom_id: str) -> str | None:
        return custom_id[len(self.DECLINE) :] if custom_id.startswith(self.DECLINE) else None

    def parse_cancel(self, custom_id: str) -> str | None:
        return custom_id[len(self.CANCEL) :] if custom_id.startswith(self.CANCEL) else None

    def parse_verify_game(self, custom_id: str) -> str | None:
        return custom_id[len(self.PF_GAME) :] if custom_id.startswith(self.PF_GAME) else None

    def parse_rain_join(self, custom_id: str) -> str | None:
        return custom_id[len(self.RAIN_JOIN) :] if custom_id.startswith(self.RAIN_JOIN) else None

    def parse_history(self, custom_id: str) -> HistoryTarget | None:
        if not custom_id.startswith(self.HISTORY):
            return None
        remainder = custom_id[len(self.HISTORY) :]
        user_id, _, page = remainder.partition(":")
        if not user_id:
            return None
        try:
            return HistoryTarget(user_id=user_id, page=int(page or "0"))
        except ValueError:
            return None
