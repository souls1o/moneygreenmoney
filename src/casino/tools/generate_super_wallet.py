from __future__ import annotations

from casino.tools.generate_solana_wallet import generate_solana_wallet, print_wallet


def generate_super_wallet() -> tuple[str, str]:
    return generate_solana_wallet()


def main() -> None:
    print_wallet(
        title="Super wallet public address:",
        env_name="SUPER_WALLET_PRIVATE_KEY",
        purpose="Holds player deposits. Withdrawals are paid from here. Settles house P&L with the house wallet.",
    )


if __name__ == "__main__":
    main()
