from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from casino.config.settings import Settings
from casino.container import ContainerFactory
from casino.db.mongo import COLLECTION_GAMES
from casino.solana_client.keys import KeypairLoader
from casino.solana_client.rpc import RENT_EXEMPT_FALLBACK_LAMPORTS, SWEEP_FEE_LAMPORTS, sweepable_lamports
from casino.utils.money import MathService, SolAmount


@dataclass(frozen=True, slots=True)
class MigrationLeg:
    kind: str
    label: str
    destination: str
    lamports: int


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    super_address: str
    house_address: str
    super_balance: int
    rent: int
    fee: int
    house_book: int
    house_on_chain: int
    player_total: int
    player_on_chain: int
    legs: tuple[MigrationLeg, ...]
    notes: tuple[str, ...]
    open_tables: int
    open_rains: int
    open_jackpots: int
    force: bool

    @property
    def transfer_count(self) -> int:
        return len(self.legs)

    @property
    def fee_budget(self) -> int:
        return self.transfer_count * self.fee

    @property
    def send_total(self) -> int:
        return sum(leg.lamports for leg in self.legs)

    @property
    def needed(self) -> int:
        return self.send_total + self.fee_budget

    @property
    def leftover(self) -> int:
        return self.super_balance - self.needed

    @property
    def blockers(self) -> tuple[str, ...]:
        reasons: list[str] = []
        player_send = sum(leg.lamports for leg in self.legs if leg.kind == "player")
        player_fees = sum(1 for leg in self.legs if leg.kind == "player") * self.fee
        if player_send + player_fees + self.rent > self.super_balance:
            short = player_send + player_fees + self.rent - self.super_balance
            reasons.append(
                f"Super wallet is short {SolAmount.from_lamports(short).to_decimal()} SOL "
                "to top up player deposit wallets after rent and transfer fees."
            )
        if not self.force and (self.open_tables or self.open_rains or self.open_jackpots):
            reasons.append(
                "Stop the bot and settle or cancel open games, rains, and jackpots first "
                "(their locked SOL is not in player balances or the house book). "
                "Re-run, or pass --force if you are sure."
            )
        return tuple(reasons)


def build_plan(
    *,
    super_address: str,
    house_address: str,
    super_balance: int,
    rent: int,
    fee: int,
    house_book: int,
    players: list[tuple[str, str, int]],
    house_on_chain: int = 0,
    player_on_chain: dict[str, int] | None = None,
    open_tables: int = 0,
    open_rains: int = 0,
    open_jackpots: int = 0,
    force: bool = False,
) -> MigrationPlan:
    already = player_on_chain or {}
    notes: list[str] = []
    legs: list[MigrationLeg] = []
    player_book = 0
    player_have = 0
    for discord_id, address, lamports in players:
        player_book += max(lamports, 0)
        have = max(int(already.get(discord_id, 0)), 0)
        player_have += have
        send = max(lamports - have, 0)
        if send <= 0:
            continue
        legs.append(
            MigrationLeg(
                kind="player",
                label=f"player {discord_id}",
                destination=address,
                lamports=send,
            )
        )
    house_book = max(house_book, 0)
    house_have = max(house_on_chain, 0)
    house_need = max(house_book - house_have, 0)
    player_send = sum(leg.lamports for leg in legs)
    player_fees = len(legs) * fee
    remaining = super_balance - rent - player_send - player_fees
    if house_need > 0:
        house_fee = fee
        house_send = min(house_need, max(remaining - house_fee, 0))
        if house_send > 0:
            legs.append(
                MigrationLeg(
                    kind="house",
                    label="house book",
                    destination=house_address,
                    lamports=house_send,
                )
            )
        if house_send < house_need:
            short = house_need - house_send
            notes.append(
                f"House wallet already holds part of the book. Super will send "
                f"{SolAmount.from_lamports(house_send).to_decimal()} SOL more; "
                f"house on-chain will still be "
                f"{SolAmount.from_lamports(short).to_decimal()} SOL below the book."
            )
    elif house_have > house_book:
        notes.append("House wallet already holds at least the house book. No house transfer.")
    return MigrationPlan(
        super_address=super_address,
        house_address=house_address,
        super_balance=super_balance,
        rent=rent,
        fee=fee,
        house_book=house_book,
        house_on_chain=house_have,
        player_total=player_book,
        player_on_chain=player_have,
        legs=tuple(legs),
        notes=tuple(notes),
        open_tables=open_tables,
        open_rains=open_rains,
        open_jackpots=open_jackpots,
        force=force,
    )


def _print_plan(plan: MigrationPlan, math: MathService) -> None:
    def sol(lamports: int) -> str:
        return f"{math.format_sol(SolAmount.from_lamports(max(lamports, 0)))} SOL"

    print(f"Super wallet: {plan.super_address}")
    print(f"On-chain:     {sol(plan.super_balance)}")
    print(f"House wallet: {plan.house_address}")
    print(f"House book:   {sol(plan.house_book)}  (already on-chain {sol(plan.house_on_chain)})")
    print(
        f"Player books: {sol(plan.player_total)}  "
        f"(already on deposit addresses {sol(plan.player_on_chain)})"
    )
    print(f"Still to send: {sol(plan.send_total)}")
    print(f"Tx fees:      {sol(plan.fee_budget)}  ({plan.transfer_count} transfers × {plan.fee} lamports)")
    print(f"Needed:       {sol(plan.needed)} plus rent {sol(plan.rent)}")
    print(f"Leftover:     {sol(max(plan.leftover, 0))} (stays in super, or send later)")
    if plan.open_tables or plan.open_rains or plan.open_jackpots:
        print(
            f"Open activity: {plan.open_tables} tables, {plan.open_rains} rains, "
            f"{plan.open_jackpots} jackpots"
        )
    print()
    if not plan.legs:
        print()
        print("Nothing to send. Player deposits and the house wallet already cover the books.")
    for index, leg in enumerate(plan.legs, start=1):
        print(f"{index}. {leg.label}: {sol(leg.lamports)} -> {leg.destination}")
    if plan.notes:
        print()
        for note in plan.notes:
            print(note)
    if plan.blockers:
        print()
        print("Blocked:")
        for reason in plan.blockers:
            print(f"- {reason}")


def _load_env_files() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for name in (".env", ".env.prod", ".env.dev"):
        path = Path(name)
        if path.is_file():
            load_dotenv(path, override=False)


def _super_wallet_secret(settings: Settings) -> str:
    if settings.super_wallet_private_key is not None:
        value = settings.super_wallet_private_key.get_secret_value().strip()
        if value:
            return value
    return os.environ.get("SUPER_WALLET_PRIVATE_KEY", "").strip()


async def _build_live_plan(*, force: bool) -> tuple[object, object, MigrationPlan]:
    _load_env_files()
    settings = Settings()  # type: ignore[call-arg]
    secret = _super_wallet_secret(settings)
    if not secret:
        raise SystemExit(
            "SUPER_WALLET_PRIVATE_KEY was not found in .env / .env.prod / .env.dev. "
            "Keep that line in the env file you are using, then run this from the same directory."
        )
    if not settings.solana_private_key or not settings.deposit_master_secret:
        raise SystemExit("Set SOLANA_PRIVATE_KEY and DEPOSIT_MASTER_SECRET first.")
    container = ContainerFactory().build(settings)
    await container.mongo.connect()
    await container.solana.connect()
    if not container.solana.is_configured or not container.solana.house_address:
        await container.mongo.close()
        await container.solana.close()
        raise SystemExit("House wallet and deposit master must be configured.")
    super_kp = KeypairLoader().load(secret)
    super_address = str(super_kp.pubkey())
    house_address = container.solana.house_address
    if super_address == house_address:
        await container.mongo.close()
        await container.solana.close()
        raise SystemExit("SUPER_WALLET_PRIVATE_KEY matches the house wallet. Nothing to split.")
    super_balance = await container.solana.get_balance(super_address)
    rent = await container.solana.get_rent_exempt_lamports()
    house_on_chain = await container.solana.get_balance(house_address)
    treasury = await container.house.get()
    users = await container.users.list_positive_balances()
    players: list[tuple[str, str, int]] = []
    player_on_chain: dict[str, int] = {}
    for user in users:
        address = user.deposit_address or container.solana.player_address(user.discord_id)
        players.append((user.discord_id, address, user.sol_balance_lamports))
        try:
            player_on_chain[user.discord_id] = await container.solana.get_balance(address)
        except Exception:
            player_on_chain[user.discord_id] = 0
        await asyncio.sleep(float(getattr(settings, "deposit_rpc_gap_seconds", 0) or 0))
    games = container.mongo.db[COLLECTION_GAMES]
    open_tables = await games.count_documents({"status": {"$in": ["pending", "active"]}})
    open_rains = len(await container.rains.list_open())
    open_jackpots = len(await container.jackpots.list_live())
    plan = build_plan(
        super_address=super_address,
        house_address=house_address,
        super_balance=super_balance,
        rent=rent or RENT_EXEMPT_FALLBACK_LAMPORTS,
        fee=SWEEP_FEE_LAMPORTS,
        house_book=treasury.sol_balance_lamports,
        players=players,
        house_on_chain=house_on_chain,
        player_on_chain=player_on_chain,
        open_tables=open_tables,
        open_rains=open_rains,
        open_jackpots=open_jackpots,
        force=force,
    )
    return container, super_kp, plan


async def _run(*, apply: bool, force: bool) -> None:
    container, super_kp, plan = await _build_live_plan(force=force)
    math = container.math
    try:
        _print_plan(plan, math)
        if not apply:
            print()
            print("Dry run only. Stop the bot, then re-run with --apply to send.")
            return
        if plan.blockers:
            raise SystemExit("Refusing to send until the blockers above are fixed.")
        if not plan.legs:
            print("Already migrated.")
            return
        spendable = sweepable_lamports(plan.super_balance, plan.rent)
        if plan.send_total > spendable:
            raise SystemExit("Super wallet cannot cover the plan after rent.")
        for leg in plan.legs:
            signature = await container.solana.send_from_keypair(
                super_kp,
                leg.destination,
                SolAmount.from_lamports(leg.lamports),
            )
            print(f"sent {leg.label}: {signature}")
        leftover = await container.solana.get_balance(plan.super_address)
        print(f"done. super leftover {math.format_sol(SolAmount.from_lamports(leftover))} SOL")
    finally:
        await container.solana.close()
        await container.mongo.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Split the old super wallet into house wallet + player deposit addresses."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Send the transfers. Default is a dry run.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow sending even if games, rains, or jackpots are still open.",
    )
    args = parser.parse_args()
    asyncio.run(_run(apply=args.apply, force=args.force))


if __name__ == "__main__":
    main()
