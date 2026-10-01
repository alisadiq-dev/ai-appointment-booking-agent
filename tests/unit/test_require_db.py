"""REQUIRE_DB=1 turns "database not configured" from a skip into a failure (used by CI)."""

from collections.abc import Mapping

import pytest

from tests.db_requirements import check_env


def test_missing_config_skips_by_default() -> None:
    with pytest.raises(pytest.skip.Exception, match="DATABASE_URL"):
        check_env("requires_db", {})


@pytest.mark.parametrize("flag", ["", "0", "false", "no"])
def test_a_falsy_require_db_still_skips(flag: str) -> None:
    with pytest.raises(pytest.skip.Exception):
        check_env("requires_db", {"REQUIRE_DB": flag})


@pytest.mark.parametrize("flag", ["1", "true", "TRUE", "yes"])
def test_require_db_fails_instead_of_skipping(flag: str) -> None:
    with pytest.raises(pytest.fail.Exception, match=r"REQUIRE_DB.*DATABASE_URL"):
        check_env("requires_db", {"REQUIRE_DB": flag})


def test_an_empty_value_counts_as_missing() -> None:
    with pytest.raises(pytest.fail.Exception):
        check_env("requires_db", {"REQUIRE_DB": "1", "DATABASE_URL": ""})


def test_configured_database_passes_either_way() -> None:
    env: Mapping[str, str] = {"REQUIRE_DB": "1", "DATABASE_URL": "postgresql://x"}

    check_env("requires_db", env)  # no exception
    check_env("requires_db", {"DATABASE_URL": "postgresql://x"})


def test_supabase_auth_tests_need_both_variables_and_name_the_missing_one() -> None:
    env = {"REQUIRE_DB": "1", "SUPABASE_URL": "http://127.0.0.1:54321"}

    with pytest.raises(pytest.fail.Exception, match="SUPABASE_PUBLISHABLE_KEY") as info:
        check_env("requires_supabase_auth", env)
    assert "SUPABASE_URL" not in str(info.value).split("missing")[-1]


def test_unmarked_tests_are_not_affected() -> None:
    check_env("some_other_marker", {"REQUIRE_DB": "1"})  # no exception


# ---- the real wiring, run in a nested pytest session ----

_NESTED_TEST = """
import pytest

@pytest.mark.requires_db
def test_needs_a_database():
    pass
"""


def _nested(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, **env: str
) -> pytest.RunResult:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REQUIRE_DB", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    pytester.makeconftest("from tests.conftest import _enforce_env_requirements  # noqa: F401")
    pytester.makepyfile(_NESTED_TEST)
    return pytester.runpytest_inprocess("-p", "no:cacheprovider")


def test_nested_session_skips_without_require_db(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _nested(pytester, monkeypatch).assert_outcomes(skipped=1)


def test_nested_session_fails_with_require_db(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _nested(pytester, monkeypatch, REQUIRE_DB="1")

    result.assert_outcomes(errors=1)  # pytest reports a failure in fixture setup as an error
    assert result.ret != 0  # the run fails, so CI goes red
    result.stdout.fnmatch_lines(["*REQUIRE_DB*DATABASE_URL*"])


def test_nested_session_runs_the_test_when_configured(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _nested(pytester, monkeypatch, REQUIRE_DB="1", DATABASE_URL="postgresql://x")

    result.assert_outcomes(passed=1)


def test_google_tests_need_both_calendar_variables() -> None:
    with pytest.raises(
        pytest.skip.Exception, match="GOOGLE_CALENDAR_ID, GOOGLE_SERVICE_ACCOUNT_JSON"
    ):
        check_env("requires_google", {})
    with pytest.raises(pytest.fail.Exception, match="GOOGLE_SERVICE_ACCOUNT_JSON"):
        check_env("requires_google", {"REQUIRE_DB": "1", "GOOGLE_CALENDAR_ID": "x"})
    check_env("requires_google", {"GOOGLE_CALENDAR_ID": "x", "GOOGLE_SERVICE_ACCOUNT_JSON": "{}"})
