import pytest

from casino.services.user_service import UserService
from casino.solana_client.wallets import DepositWalletDeriver
from casino.tools.generate_deposit_master import generate_deposit_master
from casino.utils.identifiers import IdentifierFactory
from tests.conftest import FrozenClock, StubSettings
from tests.fakes import MemoryUserRepository


@pytest.mark.asyncio
async def test_new_users_get_a_stable_deposit_address() -> None:
    _address, master = generate_deposit_master()
    deriver = DepositWalletDeriver(master)
    users = UserService(MemoryUserRepository(), IdentifierFactory(), FrozenClock(), StubSettings(), deriver)  # type: ignore[arg-type]
    first = await users.get_or_create("42")
    second = await users.get_or_create("42")
    other = await users.get_or_create("43")
    assert first.deposit_address == deriver.address_for("42")
    assert second.deposit_address == first.deposit_address
    assert other.deposit_address != first.deposit_address
