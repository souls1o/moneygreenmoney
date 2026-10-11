from __future__ import annotations

from solders.keypair import Keypair


def generate_solana_wallet() -> tuple[str, str]:
    keypair = Keypair()
    return str(keypair.pubkey()), str(keypair)


def print_wallet(*, title: str, env_name: str, purpose: str) -> None:
    address, secret = generate_solana_wallet()
    print(title)
    print(address)
    print()
    print(purpose)
    print("Paste this into .env. Never commit the secret or share it.")
    print(f"{env_name}={secret}")
