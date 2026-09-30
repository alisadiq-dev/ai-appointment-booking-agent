import os

import pytest

from tests.db_requirements import REQUIREMENTS, check_env


@pytest.fixture(autouse=True)
def _enforce_env_requirements(request: pytest.FixtureRequest) -> None:
    for marker_name in REQUIREMENTS:
        if request.node.get_closest_marker(marker_name):
            check_env(marker_name, os.environ)
