from uuid import UUID

from app.repositories.types import Conn
from app.schemas.services import Service


class ServiceRepository:
    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    def list_all(self) -> list[Service]:
        rows = self._conn.execute(
            "select id, name, duration_minutes, price from public.services order by name"
        ).fetchall()
        return [Service.model_validate(r) for r in rows]

    def get(self, service_id: UUID) -> Service | None:
        row = self._conn.execute(
            "select id, name, duration_minutes, price from public.services where id = %s",
            (service_id,),
        ).fetchone()
        return Service.model_validate(row) if row else None
