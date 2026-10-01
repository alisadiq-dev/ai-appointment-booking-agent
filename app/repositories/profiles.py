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

    def get_full_name(self, user_id: UUID) -> str | None:
        """The profile's display name (from signup metadata), or None if unset or no profile."""
        row = self._conn.execute(
            "select full_name from public.profiles where id = %s", (user_id,)
        ).fetchone()
        name = row["full_name"] if row else None
        return name.strip() or None if isinstance(name, str) else None
