from types import SimpleNamespace

from casino.solana_client.rpc import signature_entries


def test_signature_entries_accepts_solders_style_value() -> None:
    items = [SimpleNamespace(signature="sig1"), SimpleNamespace(signature="sig2")]
    assert signature_entries(SimpleNamespace(value=items)) == items


def test_signature_entries_accepts_a_bare_list() -> None:
    items = ["sig1"]
    assert signature_entries(items) == items


def test_signature_entries_rejects_public_rpc_int() -> None:
    assert signature_entries(1) is None
    assert signature_entries(SimpleNamespace(value=429)) is None
