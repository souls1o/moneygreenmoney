from casino.solana_client.deposit_parser import SolanaDepositParser


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
