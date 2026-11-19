from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass

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
    player_total: int
    legs: tuple[MigrationLeg, ...]
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
    def needed(self) -> int:
        return self.player_total + self.house_book + self.fee_budget

    @property
    def leftover(self) -> int:
        return self.super_balance - self.needed

    @property
    def blockers(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.needed + self.rent > self.super_balance:
            short = self.needed + self.rent - self.super_balance
            reasons.append(
                f"Super wallet is short {SolAmount.from_lamports(short).to_decimal()} SOL "
                "after rent and transfer fees."
            )
        if not self.force and (self.open_tables or self.open_rains or self.open_jackpots):
            reasons.append(
                "Stop the bot and settle or cancel open games, rains, and jackpots first "
                "(their locked SOL is not in player balances or the house book). "
                "Re-run, or pass --force if you are sure."
            )
        if not self.legs:
            reasons.append("Nothing to send: player balances and house book are zero.")
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
    open_tables: int = 0,
    open_rains: int = 0,
    open_jackpots: int = 0,
    force: bool = False,
) -> MigrationPlan:
    legs: list[MigrationLeg] = []
    for discord_id, address, lamports in players:
        if lamports <= 0:
            continue
        legs.append(
            MigrationLeg(
                kind="player",
                label=f"player {discord_id}",
                destination=address,
                lamports=lamports,
            )
        )
    if house_book > 0:
        legs.append(
            MigrationLeg(
                kind="house",
                label="house book",
                destination=house_address,
                lamports=house_book,
            )
        )
    return MigrationPlan(
        super_address=super_address,
        house_address=house_address,
        super_balance=super_balance,
        rent=rent,
        fee=fee,
        house_book=max(house_book, 0),
        player_total=sum(leg.lamports for leg in legs if leg.kind == "player"),
        legs=tuple(legs),
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
    print(f"House book:   {sol(plan.house_book)}")
    print(f"Player books: {sol(plan.player_total)}  ({sum(1 for leg in plan.legs if leg.kind == 'player')} wallets)")
    print(f"Tx fees:      {sol(plan.fee_budget)}  ({plan.transfer_count} transfers × {plan.fee} lamports)")
    print(f"Needed:       {sol(plan.needed)} plus rent {sol(plan.rent)}")
    print(f"Leftover:     {sol(max(plan.leftover, 0))} (stays in super, or send later)")
    if plan.open_tables or plan.open_rains or plan.open_jackpots:
        print(
            f"Open activity: {plan.open_tables} tables, {plan.open_rains} rains, "
            f"{plan.open_jackpots} jackpots"
        )
    print()
    for index, leg in enumerate(plan.legs, start=1):
        print(f"{index}. {leg.label}: {sol(leg.lamports)} -> {leg.destination}")
    if plan.blockers:
        print()
        print("Blocked:")
        for reason in plan.blockers:
            print(f"- {reason}")


async def _build_live_plan(*, force: bool) -> tuple[object, object, MigrationPlan]:
    secret = os.environ.get("SUPER_WALLET_PRIVATE_KEY", "").strip()
    if not secret:
        raise SystemExit("Set SUPER_WALLET_PRIVATE_KEY in the environment (your current .env is fine).")
    settings = Settings()  # type: ignore[call-arg]
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
    treasury = await container.house.get()
    users = await container.users.list_positive_balances()
    players: list[tuple[str, str, int]] = []
    for user in users:
        address = user.deposit_address or container.solana.player_address(user.discord_id)
        players.append((user.discord_id, address, user.sol_balance_lamports))
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
        spendable = sweepable_lamports(plan.super_balance, plan.rent)
        if plan.player_total + plan.house_book > spendable:
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
