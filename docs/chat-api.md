# Chat API

`POST /chat` runs one turn of the booking agent (see [agent.md](agent.md)).

```http
POST /chat
Authorization: Bearer <Supabase JWT>
Content-Type: application/json

{"message": "I'd like a haircut on Monday at 10am"}
```

```json
{"reply": "Shall I book a Haircut on Mon 07 Jan 2030 at 10:00? Reply yes to confirm or no to change it."}
```

The user is always the one in the token. The body has one field, `message` (1 to 1000 characters
after trimming; extra fields are rejected).

## Responses

| Status | Code | When |
|--------|------|------|
| 200 | | A reply, including business outcomes: slot taken or booking cancelled between proposal and yes (the reply offers fresh slots). |
| 401 | `unauthorized` | Missing or invalid token. Does not use up anyone's rate-limit budget. |
| 409 | `turn_in_progress` | Another message from the same user is still being processed. Retry shortly. |
| 409 | `calendar_event_missing` | The booking's Google event was deleted by hand, so it cannot be moved. See below. |
| 422 | `validation_error` | Empty, over-long, wrongly typed or extra fields. The message text is never echoed back. |
| 429 | `rate_limited` | Over the limit. `Retry-After` gives the seconds to wait. |
| 503 | `calendar_unavailable`, `database_unavailable`, `auth_unavailable` | Infrastructure failed. The whole turn was rolled back; repeat the message. |

All errors use the standard `{"error": {"code", "message"}}` body.

## One transaction per turn, session saved only on success

Each request gets one pooled database connection (the same mechanism as the booking routes). The
turn's booking writes **and** the saved conversation state happen on that connection, and the pool
commits only if the request succeeds (before the response is sent) and rolls back if anything raises.

- A calendar or database error during the turn raises, rolls back every write of that turn and the
  session save, and answers 503. The proposal is still stored from the earlier turn, so the
  customer just says "yes" again.
- Business outcomes never raise: they are replies, committed normally (the stale proposal is
  cleared and fresh slots are offered).
- `calendar_event_missing` is raised after the database change, so it also rolls back. In chat its
  message spells out the full recovery path: *"This booking's calendar entry is missing. Reply 'no'
  to clear the pending change, then ask to cancel it and book again."* (the proposal stays open
  because the turn was rolled back, so it must be answered first). The booking REST routes keep
  the shorter default wording, since they have no pending change.

Known limit (ordering): FastAPI resolves the dependencies even when the body is invalid, so a
request with an invalid body (422) still counts against the rate limit, takes a pooled connection
and the user's turn lock for an instant before being rejected. This is bounded by the rate limit
and the lock, and invalid requests being rate limited is intended, so it is accepted rather than
restructured.

Known limit (from Phase 4): if the database commit itself fails after Google accepted a create,
an orphan calendar event is left behind. It carries the booking id in its private properties.

## Rate limiting

- **Keyed by the user id from the JWT, not the IP.** Many customers can share an IP (mobile
  carriers, offices) and one customer can change IPs.
- Default **10 requests per 60 seconds** per user (`CHAT_RATE_LIMIT_REQUESTS`,
  `CHAT_RATE_LIMIT_WINDOW_SECONDS`), as a sliding window. Only allowed requests count.
- It runs after authentication and before the database connection and the model call, so a
  limited request costs almost nothing. Invalid (422) requests count, so they cannot be spammed
  for free.
- **The limit is per instance.** The counters live in the memory of one process. With N instances
  each keeps its own counters, so a user could send up to N times the limit, and a restart resets
  them. The deployment therefore runs a single instance (Cloud Run `max-instances=1`, tracked in
  tasks.md Phase 8). Moving to several instances needs a shared store (for example Redis).

## One turn per user at a time

At the start of the transaction the request takes a per-user Postgres advisory lock with
`pg_try_advisory_xact_lock(namespace, key)`:

- It is the **non-blocking** form: a second simultaneous message from the same user fails
  immediately with `409 turn_in_progress` instead of waiting, so it never holds another pooled
  connection while it waits. The rejected message never reaches the model.
- It uses the two-key form with a dedicated namespace constant (`CHAT_TURN_LOCK_NAMESPACE` in
  `app/repositories/sessions.py`). The booking write lock uses the single-key form with a different
  constant, so the two can never collide (a test inspects `pg_locks`).
- It is transaction-scoped: released at commit or rollback, so a crashed request cannot lock a user
  out. Other users are unaffected.
- Without it, two simultaneous turns would load the same stored session and the later save would
  overwrite the earlier one. (Double bookings were already impossible: the overlap constraint.)

## Trade-off: the connection is held during the model call

Because everything is one transaction, the request keeps its database connection while it waits
for Gemini (up to `GEMINI_TIMEOUT_SECONDS`, default 10 s). With the default pool (10 connections)
ten slow model calls at once would use every connection and make other requests wait for the pool
(then fail with 503 after `DB_POOL_TIMEOUT_SECONDS`). This is accepted for now because it is what
makes the turn atomic and keeps the code simple, and it is bounded by three things: the per-user
rate limit, the per-user turn lock (one connection per user), and the model timeout. If it ever
matters, the options are a larger pool, a shorter model timeout, or splitting the turn into a
read phase, the model call without a connection, and a write phase.

## Configuration

| Variable | Default | Notes |
|----------|---------|-------|
| `CHAT_RATE_LIMIT_REQUESTS` | `10` | Requests allowed per window, per user. |
| `CHAT_RATE_LIMIT_WINDOW_SECONDS` | `60` | Window length. |

Gemini and calendar settings are in [agent.md](agent.md) and [google-calendar.md](google-calendar.md).

## Tests

```bash
uv run pytest tests/integration/test_api_chat.py        # in-memory: validation, auth, rate limit
export DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
uv run pytest tests/integration/test_api_chat_db.py     # real Postgres: transactions, rollback, lock
```
