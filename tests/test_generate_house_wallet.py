from casino.solana_client.keys import KeypairLoader
from casino.solana_client.wallets import DepositWalletDeriver
from casino.tools.generate_deposit_master import generate_deposit_master
from casino.tools.generate_house_wallet import generate_house_wallet
from casino.tools.generate_super_wallet import generate_super_wallet


def test_generated_house_wallet_loads_as_solana_private_key() -> None:
    address, secret = generate_house_wallet()
    keypair = KeypairLoader().load(secret)
    assert str(keypair.pubkey()) == address
    other_address, other_secret = generate_house_wallet()
    assert secret != other_secret
    assert address != other_address


def test_loader_strips_duplicated_env_assignment() -> None:
    address, secret = generate_house_wallet()
    keypair = KeypairLoader().load(f"SOLANA_PRIVATE_KEY={secret}")
    assert str(keypair.pubkey()) == address


def test_loader_strips_super_wallet_prefix() -> None:
    address, secret = generate_super_wallet()
    keypair = KeypairLoader().load(f"SUPER_WALLET_PRIVATE_KEY={secret}")
    assert str(keypair.pubkey()) == address


def test_player_deposit_addresses_are_stable_and_unique() -> None:
    _address, master = generate_deposit_master()
    deriver = DepositWalletDeriver(master)
    first = deriver.address_for("1200925985999171706")
    again = deriver.address_for("1200925985999171706")
    other = deriver.address_for("99")
    assert first == again
    assert first != other
