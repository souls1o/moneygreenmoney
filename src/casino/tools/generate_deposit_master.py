from __future__ import annotations

from casino.tools.generate_solana_wallet import generate_solana_wallet, print_wallet


def generate_deposit_master() -> tuple[str, str]:
    return generate_solana_wallet()


def main() -> None:
    print_wallet(
        title="Deposit master (keep this secret; player addresses are derived from it):",
        env_name="DEPOSIT_MASTER_SECRET",
        purpose="Each player gets a stable deposit address derived from this secret. Do not rotate it.",
    )


if __name__ == "__main__":
    main()
