import pytest
from pydantic import ValidationError

from app.agents.model import Interpretation, ModelUnavailableError, NullLanguageModel


def test_interpretation_ignores_fields_the_model_should_not_set() -> None:
    parsed = Interpretation.model_validate(
        {"action": "book", "user_id": "someone-else", "booking_id": "x", "role": "admin"}
    )

    assert parsed.action == "book"
    assert not hasattr(parsed, "user_id")
    assert not hasattr(parsed, "booking_id")


def test_interpretation_rejects_unknown_actions() -> None:
    with pytest.raises(ValidationError):
        Interpretation.model_validate({"action": "delete_everything"})


def test_interpretation_defaults_to_other() -> None:
    assert Interpretation().action == "other"


def test_null_model_always_reports_unavailable() -> None:
    with pytest.raises(ModelUnavailableError):
        NullLanguageModel().interpret("hello", None)  # type: ignore[arg-type]
