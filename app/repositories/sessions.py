import json
from typing import Any
from uuid import UUID

from app.repositories.types import Conn

# First key of the two-key advisory lock that serializes one user's chat turns. The booking write
# lock uses the single-key form (BOOKING_WRITE_LOCK_KEY); Postgres keeps the two forms in separate
# lock spaces, and this namespace differs from that key as well, so they can never collide.
CHAT_TURN_LOCK_NAMESPACE = 6_117_002


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

    def try_lock_turn(self, user_id: UUID) -> bool:
        """Take this user's chat-turn lock for the rest of the current transaction, without
        waiting. False means another turn of the same user is already running.

        Transaction-scoped: released automatically on commit or rollback, so a crashed request
        can never leave a user locked out. Two users whose ids share the first 4 bytes would
        share a lock, which only costs a spurious "turn in progress".
        """
        second_key = int.from_bytes(user_id.bytes[:4], "big", signed=True)
        row = self._conn.execute(
            "select pg_try_advisory_xact_lock(%s::int, %s::int) as locked",
            (CHAT_TURN_LOCK_NAMESPACE, second_key),
        ).fetchone()
        return bool(row and row["locked"])
