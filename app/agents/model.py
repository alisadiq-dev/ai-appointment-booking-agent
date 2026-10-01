"""The language-model interface the agent depends on (Gemini sits behind it).

The model only INTERPRETS a message into an Interpretation. It never sees or returns a user id,
a booking id or a UUID of any kind: it can only name a service and point at one of the customer's
own bookings by its number in the list the agent showed. Code turns those into real ids.
Its output is untrusted input and is validated like any other.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

Action = Literal["book", "reschedule", "cancel", "availability", "other"]


class Interpretation(BaseModel):
    """What the customer wants, as understood by the model. Unknown fields are dropped."""

    model_config = ConfigDict(extra="ignore")

    action: Action = "other"
    service_name: str | None = None
    # 1-based number of one of the customer's own bookings, as listed in the context.
    booking_number: int | None = None
    date: str | None = None  # YYYY-MM-DD in the business time zone
    time: str | None = None  # HH:MM, 24 hour, business time zone


@dataclass(frozen=True)
class BookingChoice:
    number: int
    description: str


@dataclass(frozen=True)
class ModelContext:
    today: str  # YYYY-MM-DD in the business time zone
    weekday: str
    service_names: list[str]
    bookings: list[BookingChoice]  # only the current customer's confirmed, future bookings


class ModelUnavailableError(Exception):
    """The model could not produce a usable interpretation (down, slow, garbage, not configured)."""


class LanguageModel(Protocol):
    def interpret(self, message: str, context: ModelContext) -> Interpretation:
        """Raise ModelUnavailableError on any failure."""
        ...


class NullLanguageModel:
    """Used when no Gemini key is configured. The agent then replies with its safe fallback."""

    def interpret(self, message: str, context: ModelContext) -> Interpretation:
        raise ModelUnavailableError("no language model is configured")
