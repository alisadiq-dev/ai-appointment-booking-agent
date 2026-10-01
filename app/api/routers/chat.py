from fastapi import APIRouter

from app.api.deps import ChatAgentDep, ChatUser
from app.schemas.api import ChatRequest, ChatResponse
from app.services.errors import CalendarEventMissingError

router = APIRouter(prefix="/chat", tags=["chat"])

# In chat the customer has a proposal open, which must be answered before anything else, so the
# message spells out the whole recovery path (the REST routes keep the shorter default wording).
CALENDAR_EVENT_MISSING_MESSAGE = (
    "This booking's calendar entry is missing. Reply 'no' to clear the pending change, "
    "then ask to cancel it and book again."
)


@router.post("")
def chat(body: ChatRequest, user: ChatUser, agent: ChatAgentDep) -> ChatResponse:
    """One chat turn. The user id comes from the verified token only.

    Business outcomes (slot taken, booking cancelled meanwhile) are normal 200 replies. A calendar
    or database failure raises, which rolls back the turn's transaction (writes and saved session)
    and answers 503 in the standard error format; the customer can simply repeat their message.
    """
    try:
        reply = agent.handle(user.id, body.message)
    except CalendarEventMissingError as exc:  # still an exception, so the turn rolls back
        raise CalendarEventMissingError(CALENDAR_EVENT_MISSING_MESSAGE) from exc
    return ChatResponse(reply=reply)
