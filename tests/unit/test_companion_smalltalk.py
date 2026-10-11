"""Small talk is answered instantly with no model call; anything else is not small talk."""
import pytest

from app.modules.companion.smalltalk import BYE, GREETING, HOW_ARE_YOU, THANKS, ABOUT, detect_smalltalk, smalltalk_reply


@pytest.mark.parametrize("text,kind", [
    ("hi", GREETING), ("Hello!", GREETING), ("hey there", GREETING), ("Good morning Claude", GREETING), ("hola", GREETING),
    ("How are you?", HOW_ARE_YOU), ("hi how are you", HOW_ARE_YOU), ("What's up", HOW_ARE_YOU),
    ("thanks!", THANKS), ("thank you so much", THANKS), ("Thanks a lot 😊", THANKS),
    ("bye", BYE), ("goodnight", BYE), ("who are you", ABOUT), ("what can you do?", ABOUT),
])
def test_detects_small_talk(text, kind):
    assert detect_smalltalk(text) == kind


@pytest.mark.parametrize("text", [
    "", "ok", "yes", "sure", "hi can you plan a trip to Lisbon", "thanks for the hotel list, now remove day 3",
    "hello, what is the weather in Paris", "how are you going to get me to Rome cheaply", "x" * 200,
])
def test_everything_else_goes_to_the_model(text):
    assert detect_smalltalk(text) is None


def test_reply_uses_language_and_name():
    assert "Ada" in smalltalk_reply(GREETING, language="es", first_name="Ada")
    assert smalltalk_reply(GREETING, language="es").startswith("¡Hola!")
    assert smalltalk_reply(THANKS, language="yo")          # languages without a table fall back to English, never fail
    assert "," not in smalltalk_reply(GREETING, language="en", first_name=None).split("!")[0]
