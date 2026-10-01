"""Deterministic reading of a customer's reply to "shall I go ahead?".

The language model is deliberately NOT involved: a model can be talked into saying anything, but
this check only accepts a whole message that is exactly one of a few explicit phrases. Anything
else (including "yes, but 5pm", "ok", "sure") is Reply.OTHER and never confirms a write.

Known limitation: the accepted words are English only.
"""

import re
from enum import Enum


class Reply(Enum):
    YES = "yes"
    NO = "no"
    OTHER = "other"


_YES = frozenset(
    {
        "yes",
        "y",
        "yep",
        "yes please",
        "yes confirm",
        "yes i confirm",
        "confirm",
        "confirmed",
        "i confirm",
        "ok book it",
        "okay book it",
        "yes book it",
        "book it",
        "yes go ahead",
        "go ahead",
    }
)

_NO = frozenset(
    {
        "no",
        "n",
        "nope",
        "no thanks",
        "no thank you",
        "dont",
        "do not",
        "never mind",
        "nevermind",
        "not that one",
    }
)

_PUNCTUATION = re.compile(r"[^a-z ]")
_SPACES = re.compile(r"\s+")


def classify_reply(text: str) -> Reply:
    normalized = _SPACES.sub(" ", _PUNCTUATION.sub("", text.lower().replace("'", ""))).strip()
    if normalized in _YES:
        return Reply.YES
    if normalized in _NO:
        return Reply.NO
    return Reply.OTHER
