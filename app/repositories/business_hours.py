from app.repositories.types import Conn
from app.schemas.business_hours import BusinessHour


class BusinessHoursRepository:
    def __init__(self, conn: Conn) -> None:
        self._conn = conn

    def list_all(self) -> list[BusinessHour]:
        rows = self._conn.execute(
            "select day_of_week, open_time, close_time from public.business_hours "
            "order by day_of_week"
        ).fetchall()
        return [BusinessHour.model_validate(r) for r in rows]
