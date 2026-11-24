import pytest

from smarthome.agent.tools import UNKNOWN_INTENT, detect_intent


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("xin chào", "greeting"),
        ("XIN CHÀO!!!", "greeting"),
        ("cảm ơn bạn", "thanks"),
        ("help me", "help"),
        ("this is fine", UNKNOWN_INTENT),  # "hi" inside "this" must not match
        ("", UNKNOWN_INTENT),
    ],
)
def test_detect_intent(text: str, expected: str) -> None:
    assert detect_intent(text) == expected
