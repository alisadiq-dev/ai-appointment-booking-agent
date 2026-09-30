from typing import Any

import psycopg

# Connections are created with row_factory=dict_row (see app/core/db.py).
Conn = psycopg.Connection[dict[str, Any]]
