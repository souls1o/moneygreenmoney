from __future__ import annotations

from casino.discord_bot.presentation.embeds import EmbedFactory
from casino.discord_bot.presentation.ids import ComponentIdCodec
from casino.services.verification_service import VerificationService
from casino.utils.money import MathService
from tests.conftest import FrozenClock, StubSettings
from tests.test_profile_history import _panel_text


def test_verification_code_is_six_digits_and_single_use() -> None:
    clock = FrozenClock()
    service = VerificationService(clock)
    code = service.issue("guild", "user")
    assert len(code) == 6
    assert code.isdigit()
    assert service.check("guild", "user", code) is True
    assert service.check("guild", "user", code) is False


def test_verification_code_rejects_wrong_and_expired() -> None:
    clock = FrozenClock()
    service = VerificationService(clock)
    code = service.issue("guild", "user")
    assert service.check("guild", "user", "000000") is False
    assert service.check("guild", "other", code) is False
    clock.advance(360)
    assert service.check("guild", "user", code) is False


def test_verify_panels_include_buttons() -> None:
    class _Container:
        math = MathService(300)
        settings = StubSettings()

    factory = EmbedFactory(_Container())  # type: ignore[arg-type]
    ids = ComponentIdCodec()
    hub = _panel_text(factory.verify_hub())
    challenge = _panel_text(factory.verify_challenge("123456"))
    assert "Verify" in hub
    assert "123456" in challenge
    assert ids.VERIFY_START
    assert ids.VERIFY_ENTER
