from casino.config.settings import Settings


def test_verify_page_url_uses_independent_verifier() -> None:
    settings = Settings.model_construct(
        provably_fair_verifier_url="https://rtpspy.com/provably-fair-verifier/"
    )
    assert settings.verify_page_url() == "https://rtpspy.com/provably-fair-verifier"
