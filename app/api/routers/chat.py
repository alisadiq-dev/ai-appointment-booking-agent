from fastapi import APIRouter

from app.api.deps import ChatAgentDep, ChatUser
from app.schemas.api import ChatRequest, ChatResponse

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("")
def chat(body: ChatRequest, user: ChatUser, agent: ChatAgentDep) -> ChatResponse:
    """One chat turn. The user id comes from the verified token only.

    Business outcomes (slot taken, booking cancelled meanwhile) are normal 200 replies. A calendar
    or database failure raises, which rolls back the turn's transaction (writes and saved session)
    and answers 503 in the standard error format; the customer can simply repeat their message.
    """
    return ChatResponse(reply=agent.handle(user.id, body.message))
