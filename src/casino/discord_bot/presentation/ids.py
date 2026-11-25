from __future__ import annotations

from dataclasses import dataclass

from casino.domain.baccarat import BaccaratSide
from casino.domain.blackjack import BlackjackAction


@dataclass(frozen=True, slots=True)
class HistoryTarget:
    user_id: str
    page: int


@dataclass(frozen=True, slots=True)
class BlackjackTarget:
    game_id: str
    action: BlackjackAction


@dataclass(frozen=True, slots=True)
class MinesTileTarget:
    game_id: str
    tile: int


@dataclass(frozen=True, slots=True)
class BaccaratBetTarget:
    baccarat_id: str
    side: BaccaratSide


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
    CLIENT_SEED = "settings:client_seed"
    PF_CHECK = "pf:check"
    PF_GAME = "pf:game:"
    RAIN_JOIN = "rain:join:"
    VERIFY_START = "verify:start"
    VERIFY_ENTER = "verify:enter"
    BJ_HIT = "bj:hit:"
    BJ_STAND = "bj:stand:"
    BJ_SPLIT = "bj:split:"
    BJ_DOUBLE = "bj:double:"
    BJ_NEW = "bj:new:"
    BJ_BET = "bj:bet:"
    LB_NEW = "lb:new:"
    LB_BET = "lb:bet:"
    TARGET_LOCK = "tg:lock:"
    TICKET_TOPIC = "ticket:topic"
    TICKET_CLOSE = "ticket:close:"
    JP_JOIN = "jp:join:"
    BC_PLAYER = "bc:p:"
    BC_BANKER = "bc:b:"
    BC_TIE = "bc:t:"
    MN_TILE = "mn:t:"
    MN_CASH = "mn:cash:"
    MN_NEW = "mn:new:"
    MN_BET = "mn:bet:"
    BH_NEW = "bh:new:"
    BH_BET = "bh:bet:"
    BH_PLAYER = "bh:p:"
    BH_BANKER = "bh:b:"
    BH_TIE = "bh:t:"
    TW_TILE = "tw:t:"
    TW_CASH = "tw:cash:"
    TW_NEW = "tw:new:"
    TW_BET = "tw:bet:"

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

    def blackjack_hit(self, game_id: str) -> str:
        return f"{self.BJ_HIT}{game_id}"

    def blackjack_stand(self, game_id: str) -> str:
        return f"{self.BJ_STAND}{game_id}"

    def blackjack_split(self, game_id: str) -> str:
        return f"{self.BJ_SPLIT}{game_id}"

    def blackjack_double(self, game_id: str) -> str:
        return f"{self.BJ_DOUBLE}{game_id}"

    def blackjack_new(self, game_id: str) -> str:
        return f"{self.BJ_NEW}{game_id}"

    def blackjack_bet(self, game_id: str) -> str:
        return f"{self.BJ_BET}{game_id}"

    def parse_blackjack_new(self, custom_id: str) -> str | None:
        return custom_id[len(self.BJ_NEW) :] if custom_id.startswith(self.BJ_NEW) else None

    def parse_blackjack_bet(self, custom_id: str) -> str | None:
        return custom_id[len(self.BJ_BET) :] if custom_id.startswith(self.BJ_BET) else None

    def limbo_new(self, game_id: str) -> str:
        return f"{self.LB_NEW}{game_id}"

    def limbo_bet(self, game_id: str) -> str:
        return f"{self.LB_BET}{game_id}"

    def parse_limbo_new(self, custom_id: str) -> str | None:
        return custom_id[len(self.LB_NEW) :] if custom_id.startswith(self.LB_NEW) else None

    def parse_limbo_bet(self, custom_id: str) -> str | None:
        return custom_id[len(self.LB_BET) :] if custom_id.startswith(self.LB_BET) else None

    def target_lock(self, game_id: str) -> str:
        return f"{self.TARGET_LOCK}{game_id}"

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

    def jackpot_join(self, jackpot_id: str) -> str:
        return f"{self.JP_JOIN}{jackpot_id}"

    def parse_jackpot_join(self, custom_id: str) -> str | None:
        return custom_id[len(self.JP_JOIN) :] if custom_id.startswith(self.JP_JOIN) else None

    def parse_target_lock(self, custom_id: str) -> str | None:
        return custom_id[len(self.TARGET_LOCK) :] if custom_id.startswith(self.TARGET_LOCK) else None

    def baccarat_player(self, baccarat_id: str) -> str:
        return f"{self.BC_PLAYER}{baccarat_id}"

    def baccarat_banker(self, baccarat_id: str) -> str:
        return f"{self.BC_BANKER}{baccarat_id}"

    def baccarat_tie(self, baccarat_id: str) -> str:
        return f"{self.BC_TIE}{baccarat_id}"

    def parse_baccarat_bet(self, custom_id: str) -> BaccaratBetTarget | None:
        mapping = (
            (self.BC_PLAYER, BaccaratSide.PLAYER),
            (self.BC_BANKER, BaccaratSide.BANKER),
            (self.BC_TIE, BaccaratSide.TIE),
        )
        for prefix, side in mapping:
            if custom_id.startswith(prefix):
                baccarat_id = custom_id[len(prefix) :]
                if baccarat_id:
                    return BaccaratBetTarget(baccarat_id=baccarat_id, side=side)
        return None

    def parse_blackjack(self, custom_id: str) -> BlackjackTarget | None:
        mapping = (
            (self.BJ_HIT, BlackjackAction.HIT),
            (self.BJ_STAND, BlackjackAction.STAND),
            (self.BJ_SPLIT, BlackjackAction.SPLIT),
            (self.BJ_DOUBLE, BlackjackAction.DOUBLE),
        )
        for prefix, action in mapping:
            if custom_id.startswith(prefix):
                game_id = custom_id[len(prefix) :]
                if game_id:
                    return BlackjackTarget(game_id=game_id, action=action)
        return None

    def ticket_close(self, ticket_id: str) -> str:
        return f"{self.TICKET_CLOSE}{ticket_id}"

    def parse_ticket_close(self, custom_id: str) -> str | None:
        return custom_id[len(self.TICKET_CLOSE) :] if custom_id.startswith(self.TICKET_CLOSE) else None

    def mines_tile(self, game_id: str, tile: int) -> str:
        return f"{self.MN_TILE}{game_id}:{tile}"

    def mines_cashout(self, game_id: str) -> str:
        return f"{self.MN_CASH}{game_id}"

    def mines_new(self, game_id: str) -> str:
        return f"{self.MN_NEW}{game_id}"

    def mines_bet(self, game_id: str) -> str:
        return f"{self.MN_BET}{game_id}"

    def parse_mines_tile(self, custom_id: str) -> MinesTileTarget | None:
        if not custom_id.startswith(self.MN_TILE):
            return None
        remainder = custom_id[len(self.MN_TILE) :]
        game_id, _, tile = remainder.rpartition(":")
        if not game_id:
            return None
        try:
            return MinesTileTarget(game_id=game_id, tile=int(tile))
        except ValueError:
            return None

    def parse_mines_cashout(self, custom_id: str) -> str | None:
        return custom_id[len(self.MN_CASH) :] if custom_id.startswith(self.MN_CASH) else None

    def parse_mines_new(self, custom_id: str) -> str | None:
        return custom_id[len(self.MN_NEW) :] if custom_id.startswith(self.MN_NEW) else None

    def parse_mines_bet(self, custom_id: str) -> str | None:
        return custom_id[len(self.MN_BET) :] if custom_id.startswith(self.MN_BET) else None

    def house_baccarat_new(self, game_id: str) -> str:
        return f"{self.BH_NEW}{game_id}"

    def house_baccarat_bet(self, game_id: str) -> str:
        return f"{self.BH_BET}{game_id}"

    def parse_house_baccarat_new(self, custom_id: str) -> str | None:
        return custom_id[len(self.BH_NEW) :] if custom_id.startswith(self.BH_NEW) else None

    def parse_house_baccarat_bet(self, custom_id: str) -> str | None:
        return custom_id[len(self.BH_BET) :] if custom_id.startswith(self.BH_BET) else None

    def house_baccarat_player(self, game_id: str) -> str:
        return f"{self.BH_PLAYER}{game_id}"

    def house_baccarat_banker(self, game_id: str) -> str:
        return f"{self.BH_BANKER}{game_id}"

    def house_baccarat_tie(self, game_id: str) -> str:
        return f"{self.BH_TIE}{game_id}"

    def parse_house_baccarat_side(self, custom_id: str) -> BaccaratBetTarget | None:
        mapping = (
            (self.BH_PLAYER, BaccaratSide.PLAYER),
            (self.BH_BANKER, BaccaratSide.BANKER),
            (self.BH_TIE, BaccaratSide.TIE),
        )
        for prefix, side in mapping:
            if custom_id.startswith(prefix):
                game_id = custom_id[len(prefix) :]
                if game_id:
                    return BaccaratBetTarget(baccarat_id=game_id, side=side)
        return None

    def tower_tile(self, game_id: str, tile: int) -> str:
        return f"{self.TW_TILE}{game_id}:{tile}"

    def tower_cashout(self, game_id: str) -> str:
        return f"{self.TW_CASH}{game_id}"

    def tower_new(self, game_id: str) -> str:
        return f"{self.TW_NEW}{game_id}"

    def tower_bet(self, game_id: str) -> str:
        return f"{self.TW_BET}{game_id}"

    def parse_tower_tile(self, custom_id: str) -> MinesTileTarget | None:
        if not custom_id.startswith(self.TW_TILE):
            return None
        remainder = custom_id[len(self.TW_TILE) :]
        game_id, _, tile = remainder.rpartition(":")
        if not game_id:
            return None
        try:
            return MinesTileTarget(game_id=game_id, tile=int(tile))
        except ValueError:
            return None

    def parse_tower_cashout(self, custom_id: str) -> str | None:
        return custom_id[len(self.TW_CASH) :] if custom_id.startswith(self.TW_CASH) else None

    def parse_tower_new(self, custom_id: str) -> str | None:
        return custom_id[len(self.TW_NEW) :] if custom_id.startswith(self.TW_NEW) else None

    def parse_tower_bet(self, custom_id: str) -> str | None:
        return custom_id[len(self.TW_BET) :] if custom_id.startswith(self.TW_BET) else None

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
