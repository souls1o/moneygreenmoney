import pytest

from casino.solana_client.deposit_parser import IncompleteDepositTransaction, SolanaDepositParser


class TestSolanaDepositParser:
    def test_parses_native_transfer_to_destination(self) -> None:
        parser = SolanaDepositParser()
        destination = "Dest11111111111111111111111111111111111111"
        payload = {
            "meta": {"err": None, "preBalances": [5_000_000, 1_000_000], "postBalances": [4_000_000, 2_000_000]},
            "transaction": {
                "message": {
                    "accountKeys": ["Sender111111111111111111111111111111111111", destination],
                    "instructions": [
                        {"program": "spl-memo", "parsed": "BBDEADBEEF"},
                    ],
                }
            },
        }
        parsed = parser.parse(payload, destination=destination, signature="sig")
        assert parsed is not None
        assert parsed.lamports == 1_000_000
        assert parsed.destination == destination
        assert parsed.memo == "BBDEADBEEF"

    def test_ignores_failed_transactions(self) -> None:
        parser = SolanaDepositParser()
        payload = {"meta": {"err": {"InstructionError": [0, "Custom"]}, "preBalances": [1], "postBalances": [2]}}
        assert parser.parse(payload, destination="x", signature="s") is None

    def test_parses_versioned_transfer_via_loaded_addresses(self) -> None:
        parser = SolanaDepositParser()
        destination = "Dest11111111111111111111111111111111111111"
        payload = {
            "meta": {
                "err": None,
                "preBalances": [5_000_000, 1_000_000],
                "postBalances": [4_000_000, 2_000_000],
                "loadedAddresses": {"writable": [destination], "readonly": []},
            },
            "transaction": {
                "message": {
                    "accountKeys": ["Sender111111111111111111111111111111111111"],
                    "instructions": [],
                }
            },
        }
        parsed = parser.parse(payload, destination=destination, signature="sig")
        assert parsed is not None
        assert parsed.lamports == 1_000_000
        assert parsed.destination == destination

    def test_parses_nested_confirmed_wrapper(self) -> None:
        parser = SolanaDepositParser()
        destination = "Dest11111111111111111111111111111111111111"
        payload = {
            "slot": 1,
            "transaction": {
                "transaction": {
                    "message": {
                        "accountKeys": ["Sender111111111111111111111111111111111111", destination],
                        "instructions": [],
                    }
                },
                "meta": {"err": None, "preBalances": [5_000_000, 1_000_000], "postBalances": [4_000_000, 2_000_000]},
            },
        }
        parsed = parser.parse(payload, destination=destination, signature="sig")
        assert parsed is not None
        assert parsed.lamports == 1_000_000

    def test_parses_system_transfer_instruction_when_balances_missing(self) -> None:
        parser = SolanaDepositParser()
        destination = "Dest11111111111111111111111111111111111111"
        payload = {
            "meta": {"err": None},
            "transaction": {
                "message": {
                    "accountKeys": ["Sender111111111111111111111111111111111111", destination],
                    "instructions": [
                        {
                            "program": "system",
                            "parsed": {
                                "type": "transfer",
                                "info": {
                                    "source": "Sender111111111111111111111111111111111111",
                                    "destination": destination,
                                    "lamports": 2_500_000,
                                },
                            },
                        }
                    ],
                }
            },
        }
        parsed = parser.parse(payload, destination=destination, signature="sig")
        assert parsed is not None
        assert parsed.lamports == 2_500_000
        assert parsed.source == "Sender111111111111111111111111111111111111"

    def test_source_is_the_account_that_lost_lamports(self) -> None:
        parser = SolanaDepositParser()
        destination = "Dest11111111111111111111111111111111111111"
        super_wallet = "Super1111111111111111111111111111111111111"
        sender = "Sender111111111111111111111111111111111111"
        payload = {
            "meta": {
                "err": None,
                "preBalances": [9_000_000, 1_000_000, 8_000_000],
                "postBalances": [9_000_000, 3_000_000, 6_000_000],
            },
            "transaction": {"message": {"accountKeys": [super_wallet, destination, sender], "instructions": []}},
        }
        parsed = parser.parse(payload, destination=destination, signature="sig")
        assert parsed is not None
        assert parsed.source == sender

    def test_incomplete_payload_raises(self) -> None:
        parser = SolanaDepositParser()
        destination = "Dest11111111111111111111111111111111111111"
        payload = {
            "meta": {"err": None},
            "transaction": {"message": {"accountKeys": [destination], "instructions": []}},
        }
        with pytest.raises(IncompleteDepositTransaction):
            parser.parse(payload, destination=destination, signature="sig")
