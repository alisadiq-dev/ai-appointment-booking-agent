"""The booking agent: a LangGraph state machine that handles one chat turn.

    START -> (pending proposal?) -> confirm_gate -> execute -> END     (the ONLY way to a write)
          \\-> understand -> plan -> propose -> END                      (collect, never writes)

Safety rules, each covered by tests:
- user_id comes from the verified JWT (BookingAgent.handle's argument). Model output has no field
  that can carry an identity, and it can only pick one of THIS user's bookings by list number.
- create, reschedule and cancel run only from the `execute` node, which is reachable only from
  `confirm_gate`, which needs an explicit yes (a deterministic check, not the model) to a proposal
  made on an earlier turn, for this same user, that has not expired.
- If the model fails, the agent replies with a fixed message and changes nothing.
- Business-rule errors become friendly replies. Calendar failures are NOT caught: they propagate
  so the request's database transaction rolls back (a write may already be pending) and the
  caller answers 503. The conversation state is saved only when the turn succeeds.
"""

import contextlib
import logging
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal, TypedDict
from uuid import UUID
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.agents.confirmation import Reply, classify_reply
from app.agents.model import (
    BookingChoice,
    Interpretation,
    LanguageModel,
    ModelContext,
    ModelUnavailableError,
)
from app.agents.session import SessionStorePort
from app.core.errors import AppError
from app.schemas.bookings import Booking
from app.schemas.services import Service
from app.schemas.time_range import TimeRange
from app.services.booking_service import BookingService
from app.services.errors import (
    BookingInPastError,
    BookingLimitReachedError,
    BookingNotActiveError,
    BookingNotFoundError,
    ForbiddenError,
    InvalidSlotTimeError,
    OutsideBusinessHoursError,
    ServiceNotFoundError,
    SlotUnavailableError,
)

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 1000
PROPOSAL_TTL = timedelta(minutes=10)

FALLBACK_REPLY = (
    "Sorry, I can't process that right now. Nothing has been changed. Please try again in a moment."
)
HELP_REPLY = (
    "I can help you book an appointment, reschedule or cancel one, or check which times are free. "
    "What would you like to do?"
)

# Rule violations that happen before anything is written. CalendarUnavailableError and
# CalendarEventMissingError are deliberately absent: they can be raised after the database write,
# so they must propagate and roll the request's transaction back.
_FRIENDLY_ERRORS = (
    SlotUnavailableError,
    BookingLimitReachedError,
    BookingNotFoundError,
    BookingNotActiveError,
    BookingInPastError,
    OutsideBusinessHoursError,
    InvalidSlotTimeError,
    ServiceNotFoundError,
    ForbiddenError,
)

_TIME_FORMAT = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class AgentState(TypedDict, total=False):
    user_id: str  # from the JWT, set by BookingAgent.handle only
    message: str
    draft: dict[str, Any]  # details collected so far (ids resolved by code, never by the model)
    pending: dict[str, Any] | None  # the proposal awaiting an explicit yes
    confirmed: bool  # set only by confirm_gate, never persisted
    reply: str
    outcome: Literal["booked", "rescheduled", "cancelled"] | None


class BookingAgent:
    def __init__(
        self,
        *,
        bookings: BookingService,
        model: LanguageModel,
        sessions: SessionStorePort,
        zone: ZoneInfo,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._bookings = bookings
        self._model = model
        self._sessions = sessions
        self._zone = zone
        self._clock = clock
        self._graph = self._build_graph()

    # ------------------------------------------------------------------ entry point

    def handle(self, user_id: UUID, message: str) -> str:
        """Run one chat turn for `user_id` (taken from the verified token) and return the reply."""
        stored = self._sessions.load(user_id)
        result = self._graph.invoke(
            {
                "user_id": str(user_id),
                "message": message[:MAX_MESSAGE_CHARS],
                "draft": stored.get("draft") or {},
                "pending": stored.get("pending"),
            }
        )
        self._persist(user_id, result)
        return result["reply"]

    def _persist(self, user_id: UUID, result: AgentState) -> None:
        draft, pending = result.get("draft") or {}, result.get("pending")
        if result.get("outcome") or not (draft or pending):
            self._sessions.clear(user_id)  # finished (or nothing to remember): next chat is fresh
        else:
            self._sessions.save(user_id, {"draft": draft, "pending": pending})

    # ------------------------------------------------------------------ graph

    def _build_graph(self) -> CompiledStateGraph:
        graph = StateGraph(AgentState)
        graph.add_node("understand", self._understand)
        graph.add_node("plan", self._plan)
        graph.add_node("propose", self._propose)
        graph.add_node("confirm_gate", self._confirm_gate)
        graph.add_node("execute", self._execute)
        graph.add_conditional_edges(
            START, self._entry, {"understand": "understand", "confirm_gate": "confirm_gate"}
        )
        return graph.compile()

    def _entry(self, state: AgentState) -> Literal["understand", "confirm_gate"]:
        pending = state.get("pending")
        if pending and self._pending_is_live(pending, state["user_id"]):
            return "confirm_gate"
        return "understand"

    def _pending_is_live(self, pending: dict[str, Any], user_id: str) -> bool:
        if pending.get("user_id") != user_id:
            logger.warning("Ignoring a stored proposal that belongs to a different user")
            return False
        try:
            proposed_at = datetime.fromisoformat(pending["proposed_at"])
        except (KeyError, TypeError, ValueError):
            return False
        return self._now() - proposed_at <= PROPOSAL_TTL

    # ------------------------------------------------------------------ helpers

    def _now(self) -> datetime:
        return self._clock()

    def _local(self, moment: datetime) -> str:
        local = moment.astimezone(self._zone)
        return f"{local:%a %d %b %Y} at {local:%H:%M}"

    def _my_bookings(self, user_id: UUID) -> list[Booking]:
        """The customer's own confirmed, upcoming bookings, soonest first."""
        now = self._now()
        mine = [
            b
            for b in self._bookings.list_my_bookings(user_id)
            if b.status == "confirmed" and b.start_at > now
        ]
        return sorted(mine, key=lambda b: b.start_at)

    def _describe(self, booking: Booking, services: dict[UUID, Service]) -> str:
        service = services.get(booking.service_id)
        return f"{service.name if service else 'Appointment'} on {self._local(booking.start_at)}"

    def _free_times(self, service_id: UUID, day: date) -> list[TimeRange]:
        return self._bookings.get_availability(service_id, day)

    def _format_free(self, slots: list[TimeRange], limit: int = 8) -> str:
        times = [f"{s.start.astimezone(self._zone):%H:%M}" for s in slots[:limit]]
        more = " and more" if len(slots) > limit else ""
        return ", ".join(times) + more

    # ------------------------------------------------------------------ nodes

    def _understand(self, state: AgentState) -> Command[Literal["plan", "__end__"]]:
        user_id = UUID(state["user_id"])
        message = state["message"]
        draft = dict(state.get("draft") or {})

        if classify_reply(message) is Reply.YES:
            # A bare "yes" with no live proposal confirms nothing.
            return Command(
                update={
                    "reply": "There is nothing waiting for your confirmation. " + HELP_REPLY,
                    "draft": draft,
                },
                goto=END,
            )

        services = {s.id: s for s in self._bookings.list_services()}
        mine = self._my_bookings(user_id)
        local_now = self._now().astimezone(self._zone)
        context = ModelContext(
            today=local_now.date().isoformat(),
            weekday=f"{local_now:%A}",
            service_names=[s.name for s in services.values()],
            bookings=[
                BookingChoice(i, self._describe(b, services)) for i, b in enumerate(mine, start=1)
            ],
        )
        try:
            interpretation = self._model.interpret(message, context)
        except ModelUnavailableError:
            return Command(
                update={"reply": FALLBACK_REPLY, "draft": draft, "pending": None}, goto=END
            )

        draft = self._merge(draft, interpretation, services, mine)
        return Command(update={"draft": draft, "pending": None}, goto="plan")

    def _merge(
        self,
        draft: dict[str, Any],
        found: Interpretation,
        services: dict[UUID, Service],
        mine: list[Booking],
    ) -> dict[str, Any]:
        """Fold what the model understood into the draft. Everything is checked here, in code."""
        if found.action != "other" and found.action != draft.get("action"):
            draft.pop("booking_id", None)
            draft["action"] = found.action
        if found.service_name:
            wanted = found.service_name.strip().casefold()
            for service in services.values():
                if service.name.casefold() == wanted:
                    draft["service_id"] = str(service.id)
        if found.booking_number is not None and 1 <= found.booking_number <= len(mine):
            draft["booking_id"] = str(mine[found.booking_number - 1].id)
        if found.date:
            with contextlib.suppress(ValueError):
                draft["date"] = date.fromisoformat(found.date).isoformat()
        if found.time and _TIME_FORMAT.match(found.time):
            draft["time"] = found.time
        return draft

    def _plan(self, state: AgentState) -> Command[Literal["propose", "__end__"]]:
        user_id = UUID(state["user_id"])
        draft = dict(state.get("draft") or {})
        action = draft.get("action")
        services = {s.id: s for s in self._bookings.list_services()}

        def ask(
            text: str, new_draft: dict[str, Any] | None = None
        ) -> Command[Literal["propose", "__end__"]]:
            keep = draft if new_draft is None else new_draft
            return Command(update={"reply": text, "draft": keep}, goto=END)

        if action is None:
            return ask(HELP_REPLY)

        if action == "book":
            # Say so before asking for details, not after the customer has said yes. Only a
            # courtesy: the service enforces the limit again when the booking is written.
            limit = self._bookings.max_active_bookings
            if len(self._my_bookings(user_id)) >= limit:
                return self._stale_proposal_reply(BookingLimitReachedError(limit), {}, {})

        if action in ("reschedule", "cancel"):
            mine = self._my_bookings(user_id)
            chosen = next((b for b in mine if str(b.id) == draft.get("booking_id")), None)
            if chosen is None:
                draft.pop("booking_id", None)
                if not mine:
                    return ask("You have no upcoming bookings.", new_draft={})
                if len(mine) > 1:
                    lines = "\n".join(
                        f"{i}. {self._describe(b, services)}" for i, b in enumerate(mine, start=1)
                    )
                    return ask(f"Which booking do you mean?\n{lines}")
                draft["booking_id"] = str(mine[0].id)
            if action == "cancel":
                return Command(update={"draft": draft}, goto="propose")
            draft["service_id"] = str(
                next(b for b in mine if str(b.id) == draft["booking_id"]).service_id
            )
        elif "service_id" not in draft:
            names = ", ".join(s.name for s in services.values())
            return ask(f"Which service would you like? We offer: {names}.")

        if "date" not in draft:
            return ask("Which date would you like?")
        if action == "availability":
            slots = self._free_times(UUID(draft["service_id"]), date.fromisoformat(draft["date"]))
            if not slots:
                return ask("There are no free times on that day.", new_draft=draft)
            return ask(f"Free times that day: {self._format_free(slots)}.", new_draft=draft)
        if "time" not in draft:
            return ask("What time would you like?")
        return Command(update={"draft": draft}, goto="propose")

    def _propose(self, state: AgentState) -> Command[Literal["__end__"]]:
        user_id = UUID(state["user_id"])
        draft = dict(state.get("draft") or {})
        action = draft["action"]
        services = {s.id: s for s in self._bookings.list_services()}

        if action == "cancel":
            booking = next(
                b for b in self._my_bookings(user_id) if str(b.id) == draft["booking_id"]
            )
            summary = f"cancel your {self._describe(booking, services)}"
            start_iso = None
            service_id = str(booking.service_id)
        else:
            service_id = draft["service_id"]
            service = services[UUID(service_id)]
            day = date.fromisoformat(draft["date"])
            hour, minute = (int(part) for part in draft["time"].split(":"))
            start = datetime.combine(day, time(hour, minute), tzinfo=self._zone).astimezone(UTC)
            if not self._slot_is_open(user_id, draft, service, day, start):
                slots = self._free_times(service.id, day)
                draft.pop("time", None)
                where = (
                    f"Free times that day: {self._format_free(slots)}."
                    if slots
                    else "There are no free times on that day."
                )
                return Command(
                    update={"reply": f"Sorry, that time isn't available. {where}", "draft": draft},
                    goto=END,
                )
            start_iso = start.isoformat()
            if action == "book":
                summary = f"book a {service.name} on {self._local(start)}"
            else:
                summary = f"move your booking to {service.name} on {self._local(start)}"

        pending = {
            "action": action,
            "service_id": service_id,
            "booking_id": draft.get("booking_id"),
            "start_at": start_iso,
            "user_id": str(user_id),
            "proposed_at": self._now().isoformat(),
            "summary": summary,
        }
        reply = f"Shall I {summary}? Reply yes to confirm or no to change it."
        return Command(update={"pending": pending, "reply": reply, "draft": draft}, goto=END)

    def _slot_is_open(
        self, user_id: UUID, draft: dict[str, Any], service: Service, day: date, start: datetime
    ) -> bool:
        if any(slot.start == start for slot in self._free_times(service.id, day)):
            return True
        if draft["action"] != "reschedule":
            return False
        # Availability counts the booking being moved as busy, so a start overlapping its own
        # slot is accepted here. The service re-checks everything (other bookings, calendar)
        # when the change is executed.
        end = start + timedelta(minutes=service.duration_minutes)
        own = next(
            (b for b in self._my_bookings(user_id) if str(b.id) == draft["booking_id"]), None
        )
        return own is not None and own.start_at < end and start < own.end_at

    def _confirm_gate(self, state: AgentState) -> Command[Literal["execute", "__end__"]]:
        """Reads the customer's answer to a live proposal. Only an explicit yes goes on."""
        pending = state["pending"] or {}
        draft = dict(state.get("draft") or {})
        answer = classify_reply(state["message"])
        if answer is Reply.YES:
            return Command(update={"confirmed": True}, goto="execute")
        if answer is Reply.NO:
            if pending.get("action") == "cancel":
                return Command(
                    update={
                        "reply": "Okay, I haven't cancelled anything.",
                        "pending": None,
                        "draft": {},
                    },
                    goto=END,
                )
            draft.pop("time", None)
            return Command(
                update={
                    "reply": "No problem, I haven't booked anything. "
                    "What other time would you like?",
                    "pending": None,
                    "draft": draft,
                },
                goto=END,
            )
        return Command(
            update={
                "reply": "I'm waiting for your answer: shall I "
                f"{pending.get('summary', 'proceed')}? Reply yes to confirm or no to change it.",
            },
            goto=END,
        )

    def _execute(self, state: AgentState) -> Command[Literal["__end__"]]:
        """The only place that writes. Runs what was proposed and confirmed, nothing else."""
        pending = state.get("pending") or {}
        draft = dict(state.get("draft") or {})
        if not state.get("confirmed") or pending.get("user_id") != state.get("user_id"):
            return Command(
                update={
                    "reply": "I need your explicit confirmation first. Nothing has been changed.",
                    "pending": None,
                },
                goto=END,
            )
        user_id = UUID(state["user_id"])  # from the token, never from the model
        action = pending["action"]
        try:
            if action == "book":
                start = datetime.fromisoformat(pending["start_at"])
                self._bookings.create_booking(user_id, UUID(pending["service_id"]), start)
                reply, outcome = (
                    f"Done! Your appointment is booked: {pending['summary']}.",
                    "booked",
                )
            elif action == "reschedule":
                start = datetime.fromisoformat(pending["start_at"])
                self._bookings.reschedule_booking(user_id, UUID(pending["booking_id"]), start)
                reply, outcome = "Done! Your booking has been moved.", "rescheduled"
            else:
                self._bookings.cancel_booking(user_id, UUID(pending["booking_id"]))
                reply, outcome = "Done! Your booking has been cancelled.", "cancelled"
        except _FRIENDLY_ERRORS as exc:
            return self._stale_proposal_reply(exc, pending, draft)
        return Command(
            update={"reply": reply, "outcome": outcome, "pending": None, "draft": {}},
            goto=END,
        )

    def _stale_proposal_reply(
        self, exc: AppError, pending: dict[str, Any], draft: dict[str, Any]
    ) -> Command[Literal["__end__"]]:
        """The world changed between proposal and yes (slot taken, booking cancelled meanwhile).

        These are business outcomes, not failures: nothing was written, the stale proposal is
        dropped and, where a new time makes sense, fresh free slots are offered. Calendar and
        database errors never reach here; they propagate so the request rolls back.
        """
        service_id = pending.get("service_id")
        offers_slots = pending.get("start_at") is not None and (
            isinstance(exc, SlotUnavailableError | BookingNotActiveError)
        )
        text = f"{exc.message} Nothing has been changed. "
        if not offers_slots or service_id is None:
            return Command(
                update={
                    "reply": text + "What would you like to do?",
                    "pending": None,
                    "draft": {},
                },
                goto=END,
            )
        day = datetime.fromisoformat(pending["start_at"]).astimezone(self._zone).date()
        slots = self._free_times(UUID(service_id), day)
        if slots:
            text += f"Free times that day: {self._format_free(slots)}. Which would you like?"
        else:
            text += "There are no free times that day. Which other date would you like?"
        if isinstance(exc, SlotUnavailableError):
            draft.pop("time", None)  # same booking and day, just pick another time
            new_draft = draft
        else:  # the booking is gone: continue as a fresh booking of the same service and day
            new_draft = {"service_id": service_id, "date": day.isoformat()}
        return Command(update={"reply": text, "pending": None, "draft": new_draft}, goto=END)
