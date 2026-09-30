from uuid import UUID

from app.repositories.types import Conn


class ProfileRepository:
    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    def get_role(self, user_id: UUID) -> str | None:
        """The profile's role ('customer' or 'admin'), or None if there is no profile."""
        row = self._conn.execute(
            "select role from public.profiles where id = %s", (user_id,)
        ).fetchone()
        return row["role"] if row else None
