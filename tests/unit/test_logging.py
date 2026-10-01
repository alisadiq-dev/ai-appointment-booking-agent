import io
import json
import logging

import pytest

from app.core.logging import JsonFormatter, configure_logging, request_id_var


def _record(message: str = "hello", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, message, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formatter_emits_one_json_object_per_record() -> None:
    line = JsonFormatter().format(_record("hello"))

    data = json.loads(line)
    assert "\n" not in line
    assert data["level"] == "INFO"
    assert data["logger"] == "app.test"
    assert data["message"] == "hello"
    assert data["timestamp"].endswith("+00:00")


def test_formatter_includes_the_request_id_from_context() -> None:
    token = request_id_var.set("req-abc12345")
    try:
        data = json.loads(JsonFormatter().format(_record()))
    finally:
        request_id_var.reset(token)

    assert data["request_id"] == "req-abc12345"


def test_formatter_omits_request_id_outside_a_request() -> None:
    assert "request_id" not in json.loads(JsonFormatter().format(_record()))


def test_explicit_request_id_on_the_record_wins_over_the_context() -> None:
    token = request_id_var.set("from-context")
    try:
        data = json.loads(JsonFormatter().format(_record(request_id="from-record")))
    finally:
        request_id_var.reset(token)

    assert data["request_id"] == "from-record"


def test_formatter_includes_the_access_fields_and_nothing_else_extra() -> None:
    record = _record("request", method="GET", route="/x", status=200, duration_ms=1.5, other="s")

    data = json.loads(JsonFormatter().format(record))

    assert (data["method"], data["route"], data["status"], data["duration_ms"]) == (
        "GET",
        "/x",
        200,
        1.5,
    )
    assert "other" not in data


def test_formatter_puts_exceptions_in_one_field_so_a_log_line_stays_one_line() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            "app.test", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )

    line = JsonFormatter().format(record)

    assert "\n" not in line
    assert "RuntimeError: boom" in json.loads(line)["exception"]


def test_message_with_newlines_cannot_forge_a_second_log_line() -> None:
    line = JsonFormatter().format(_record('x\n{"level": "CRITICAL", "message": "forged"}'))

    assert "\n" not in line
    assert json.loads(line)["level"] == "INFO"


def test_configure_logging_writes_json_to_the_given_stream_and_is_idempotent() -> None:
    stream = io.StringIO()
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        configure_logging("INFO", stream=stream)
        configure_logging("INFO", stream=stream)  # second call must not double the handler
        logging.getLogger("app.somewhere").info("once")
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)

    lines = stream.getvalue().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["message"] == "once"


def test_configure_logging_rejects_an_unknown_level() -> None:
    with pytest.raises(ValueError, match="LOG_LEVEL"):
        configure_logging("LOUD")
