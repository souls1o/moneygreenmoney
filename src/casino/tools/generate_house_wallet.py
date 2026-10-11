from __future__ import annotations

from casino.tools.generate_solana_wallet import generate_solana_wallet, print_wallet


def generate_house_wallet() -> tuple[str, str]:
    return generate_solana_wallet()


def main() -> None:
    print_wallet(
        title="House deposit address:",
        env_name="SOLANA_PRIVATE_KEY",
        purpose="SOL sent here credits house reserves, then sweeps into the super wallet.",
    )


if __name__ == "__main__":
    main()
