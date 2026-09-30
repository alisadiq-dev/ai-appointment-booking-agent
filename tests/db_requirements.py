"""Environment requirements for tests that need a real database or Supabase Auth.

By default, missing configuration skips those tests so the suite runs anywhere. With
REQUIRE_DB=1 (set by CI) it fails them instead, so a misconfigured pipeline cannot go green
by silently skipping everything.
"""

from collections.abc import Mapping

import pytest

REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "requires_db": ("DATABASE_URL",),
    "requires_supabase_auth": ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY"),
}


def _require_db_flag(env: Mapping[str, str]) -> bool:
    return env.get("REQUIRE_DB", "").strip().lower() in {"1", "true", "yes"}


def check_env(marker_name: str, env: Mapping[str, str]) -> None:
    """Skip (or, with REQUIRE_DB, fail) if the marker's environment variables are missing."""
    missing = [name for name in REQUIREMENTS.get(marker_name, ()) if not env.get(name)]
    if not missing:
        return
    names = ", ".join(missing)
    if _require_db_flag(env):
        pytest.fail(
            f"REQUIRE_DB is set but required configuration is missing: {names}", pytrace=False
        )
    pytest.skip(f"set {names} to run this test")
