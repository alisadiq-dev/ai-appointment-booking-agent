import json
from typing import Any
from uuid import UUID

from app.repositories.types import Conn


class SessionRepository:
    """conversation_sessions: one row per user. Always filters by user_id (the backend role
    bypasses RLS)."""

    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    def load(self, user_id: UUID) -> dict[str, Any]:
        row = self._conn.execute(
            "select state from public.conversation_sessions where user_id = %s", (user_id,)
        ).fetchone()
        state = row["state"] if row else None
        return state if isinstance(state, dict) else {}

    def save(self, user_id: UUID, state: dict[str, Any]) -> None:
        self._conn.execute(
            """
            insert into public.conversation_sessions (user_id, state)
            values (%s, %s::jsonb)
            on conflict (user_id) do update set state = excluded.state
            """,
            (user_id, json.dumps(state)),
        )

    def clear(self, user_id: UUID) -> None:
        self._conn.execute(
            "delete from public.conversation_sessions where user_id = %s", (user_id,)
        )
