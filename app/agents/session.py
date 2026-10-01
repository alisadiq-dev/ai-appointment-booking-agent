from typing import Any, Protocol
from uuid import UUID


class SessionStorePort(Protocol):
    """Per-user conversation state (one row per user). Every call is scoped to one user id."""

    def load(self, user_id: UUID) -> dict[str, Any]: ...

    def save(self, user_id: UUID, state: dict[str, Any]) -> None: ...

    def clear(self, user_id: UUID) -> None: ...
