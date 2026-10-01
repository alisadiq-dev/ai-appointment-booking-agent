# Booking agent (LangGraph + Gemini)

`app/agents/` handles one chat turn: read the customer's message, collect what is missing, propose
an action, and only after an explicit yes on a later turn create, reschedule or cancel a booking.
The HTTP endpoint is `POST /chat`, documented in [chat-api.md](chat-api.md).

## Turn flow

```
START -- live proposal for this user? --> confirm_gate --yes--> execute --> END   (only write path)
   \                                          |--no / other--> END
    `--> understand --> plan --> propose --> END                                  (never writes)
```

| Node | Job |
|------|-----|
| `understand` | Asks the language model to interpret the message. Fixed fallback reply if it fails. |
| `plan` | Works out what is missing (service, booking, date, time) and asks one question. |
| `propose` | Checks the slot is free, stores a proposal and asks "Shall I ...? yes / no". |
| `confirm_gate` | Reads the answer to a live proposal with a deterministic check (not the model). |
| `execute` | Runs the confirmed proposal through `BookingService`. |

Conversation state (`draft` and `pending`) is stored per user in `conversation_sessions` and is
reset when a booking, reschedule or cancellation completes.

## The confirmation gate

No create, reschedule or cancel happens without an explicit yes on the **next turn**:

- `execute` is reachable only from `confirm_gate` (a test inspects the compiled graph).
- `confirm_gate` accepts a whole message that is exactly one of a few phrases (`yes`, `yes please`,
  `confirm`, `ok book it` ...). `ok`, `sure`, `yes but 5pm` and `yes and cancel the rest` are NOT
  a yes: the proposal stays and the agent asks again.
- A message that proposes and confirms at once cannot book: the proposal is made on one turn, the
  yes is read on the next.
- A clear **no** clears the proposal and asks for another time (a cancel proposal just stays as is).
- A proposal expires after 10 minutes, belongs to one user, and is replaced by any newer one.
- `execute` also refuses unless the gate set an in-memory `confirmed` flag, which is never stored.

### Known limitation: English-only confirmation words

The yes/no phrases are English only. A customer who answers `si`, `haan` or `oui` gets the
"reply yes to confirm" prompt again. This is deliberate: the gate stays a small, auditable
allow-list. Adding a language means adding its phrases to `app/agents/confirmation.py` and tests.

## Prompt injection and cross-user safety

- `user_id` comes from the verified JWT (the argument of `BookingAgent.handle`). The model's output
  (`Interpretation`) has no field for an identity, and extra fields are dropped.
- The model never sees or returns a booking id. For reschedule and cancel it can only give the
  number of one of **the current user's own** upcoming bookings; code maps the number to the id.
  A number outside that list is ignored.
- Service names are matched in code against the real service list; anything else is ignored.
- Dates and times from the model are parsed strictly; malformed values are ignored.
- The user's text is sent to Gemini as the message content, never inside the system prompt.
- Tests (`tests/unit/agents/test_agent_injection.py`): instruction overrides, "book for another
  user", cancelling another user's booking id or number, tampered stored proposals, and a model
  that claims the customer already confirmed. All end with no unauthorised write.

## Failure behaviour

- **Gemini down, slow, invalid output, or no `GEMINI_API_KEY`**: the agent replies "Sorry, I can't
  process that right now. Nothing has been changed." and never guesses an intent.
- **The world changed between proposal and yes** (someone took the slot, or the booking was
  cancelled meanwhile: `SlotUnavailableError`, `BookingNotActiveError`) is a business outcome, not
  a failure. Nothing is written, the stale proposal is cleared and the reply offers fresh free
  slots for the same service and day. After a taken slot the conversation continues (same
  booking, pick another time); after a vanished booking it continues as a fresh booking of that
  service and day. Other rule errors (booking not found, past, outside hours ...) give a friendly
  reply and reset the draft.
- **Infrastructure failures** (calendar or database errors, including while listing the fresh
  slots) are not caught. They may happen after the database write, so they
  propagate and the request's transaction rolls back (strict sync, see
  [google-calendar.md](google-calendar.md)). The conversation state is saved only when the turn
  succeeds, so the customer can simply say yes again.

## Configuration

| Variable | Default | Notes |
|----------|---------|-------|
| `GEMINI_API_KEY` | unset | Secret. Unset means the fallback reply only. Keep it in `~/.secrets/booking-agent.env`. |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Any Gemini model id that supports JSON output. |
| `GEMINI_TIMEOUT_SECONDS` | `10` | HTTP timeout for one call. |

The model sits behind `LanguageModel` (`app/agents/model.py`). `GeminiModel` is the real one,
`NullLanguageModel` is used without a key, tests use a scripted fake.

## Tests

```bash
uv run pytest tests/unit/agents                       # no network, no database

# Real Postgres (agent + repositories + session table):
export DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
uv run pytest tests/integration/test_agent_db.py tests/integration/test_session_repository.py

# Opt-in, real Gemini (source the key inside the command; never print it):
( set -a; source ~/.secrets/booking-agent.env; set +a; uv run pytest -m live_gemini )
```

## Known limits

- One conversation per user (the table has one row per user).
- The agent asks one question per turn; it does not handle several bookings in one message.
- Free-time lists show the first 8 slots of a day.
