# AI Appointment Booking Agent

A backend for a small barbershop receptionist. Customers chat in plain English ("a haircut on
Monday at 10am"); a LangGraph agent powered by Gemini collects the details, **proposes** a
booking, and only creates it after an explicit "yes" on the next turn. Bookings are stored in
Postgres and synced to Google Calendar. There is also a plain REST API for the same operations.

- **Live API:** <https://ai-booking-agent-68b73.containers.snapdeploy.app>
- **Swagger UI:** <https://ai-booking-agent-68b73.containers.snapdeploy.app/docs>

> **Cold start.** The live API runs on a free tier that sleeps after 15 idle minutes. The first
> request after a sleep can take about 30 seconds (29 s measured); later ones are fast.
> Sign-ups are closed in production, so you need a demo token to call it (see
> [Try the live API](#try-the-live-api)).

## What it does

- Lists services and business hours, and computes free slots on a 15-minute grid in the business
  time zone (default `Asia/Karachi`), taking manual Google Calendar events into account.
- Creates, reschedules and cancels bookings, through REST (`/bookings`) or chat (`POST /chat`).
- Never lets two confirmed bookings overlap: the database enforces it with an exclusion constraint.
- Keeps each customer's data private: every query filters by the user id from the verified token.
- Gives admins a read-only view of all bookings (`GET /admin/bookings`).

## Architecture

```mermaid
flowchart LR
    C[Client / Swagger] -->|Bearer JWT| API[FastAPI on SnapDeploy]
    API -->|verify via JWKS ES256| SA[Supabase Auth]
    API --> SVC[BookingService]
    API --> AG[LangGraph agent]
    AG -->|interpret message| GEM[Gemini]
    AG --> SVC
    SVC --> REPO[Repositories: raw SQL, filter by user_id]
    REPO -->|session pooler| PG[(Supabase Postgres)]
    SVC -->|strict sync, DB first Google last| GC[Google Calendar]
```

Layers are strict: routers are thin, `BookingService` holds the rules, repositories hold the SQL,
and Google and Gemini sit behind small interfaces (`CalendarPort`, `LanguageModel`) so tests use
fakes. The API only *verifies* Supabase tokens; it has no login endpoint.

### Agent graph

```mermaid
flowchart TD
    S([New message]) --> Q{Live proposal<br/>for this user?}
    Q -- no --> U[understand<br/>Gemini reads the message]
    U --> P[plan<br/>what is missing?]
    P --> PR[propose<br/>check slot, store proposal,<br/>ask yes / no]
    PR --> E1([Reply - never writes])
    Q -- yes --> G[confirm_gate<br/>deterministic yes/no check]
    G -- explicit yes --> X[execute<br/>BookingService write]
    G -- no / unclear --> E2([Reply])
    X --> E3([Reply])
```

Conversation state lives in our own `conversation_sessions` table (one row per user), so the
confirmation gate is a stored proposal rather than a LangGraph `interrupt()`. Details:
[docs/agent.md](docs/agent.md).

## Tech stack

Python 3.12, FastAPI, LangGraph, Gemini (`google-genai`, default `gemini-3.5-flash-lite`),
Postgres with psycopg 3 (raw SQL, connection pool), Supabase (Auth and Postgres), Google
Calendar API (service account), uv, Ruff, pytest and pgTAP, Docker, GitHub Actions,
SnapDeploy (free container hosting).

## How the confirmation gate works

No create, reschedule or cancel happens on the turn that asks for it.

1. The agent proposes ("Shall I book a Haircut on ... at 10:00? Reply yes to confirm") and stores
   the proposal for that user.
2. On the **next** turn, `confirm_gate` reads the answer with a **deterministic allow-list**, not
   the model. Only a whole message such as `yes`, `yes please` or `confirm` counts. `ok`, `sure`
   and `yes but 5pm` do not.
3. Only then does `execute` run. It is reachable *only* from the gate (a test inspects the
   compiled graph), refuses without an in-memory flag that is never stored, and proposals expire
   after 10 minutes.

If the world changed in between (someone took the slot), nothing is written and the agent offers
fresh slots. If Gemini fails or has no key, the reply is a fixed "nothing has been changed".

## Security

OWASP API Security Top 10 review with evidence per item: [docs/security.md](docs/security.md).

- **Auth:** Supabase JWTs verified against the JWKS (ES256/RS256 only, never HS256), with issuer,
  audience and expiry checks. Anonymous-sign-in tokens are rejected. The JWKS fetch has a
  timeout and cache and fails closed (503).
- **Identity and admin:** the user is always the token's `sub`. Admin rights come only from
  `profiles.role` in the database, never from a token claim (a test proves admin-looking claims
  get 403).
- **Isolation:** the backend role bypasses RLS, so every repository query filters by `user_id`
  itself. RLS and grants are a second layer; pgTAP fails CI if `anon` gets any table privilege.
  In production the Supabase Data API is switched off.
- **Prompt injection:** the model has no field for an identity and never sees a booking id; it
  can only point at one of the user's own bookings by list number. Tests cover instruction
  overrides, "book for another user" and tampered proposals; all end with no write.
- **Abuse limits:** `/chat` is rate limited to 10 requests per 60 seconds per user, one chat turn
  per user at a time, at most 3 upcoming bookings per user, 32 KiB request body cap, 1000
  characters per message.
- **Hygiene:** secrets are `SecretStr`, validation errors never echo values, the Authorization
  header and chat text are never logged, security headers on every response, HSTS in production.

## Run it locally

Needs [uv](https://docs.astral.sh/uv/), Docker and the [Supabase CLI](https://supabase.com/docs/guides/cli).

```bash
uv sync

# local Supabase (applies the migrations and seed; needs a one-time signing key)
echo '[]' > supabase/signing_keys.json
supabase gen signing-key --algorithm ES256 --append
supabase start

# local settings: turns on the defaults commented out in .env.example (writes .env)
sed -E 's/^# (APP_ENV|LOG_LEVEL|SUPABASE_URL|DATABASE_URL|BUSINESS_TIMEZONE|SLOT_INTERVAL_MINUTES|CALENDAR_ENABLED)=/\1=/' .env.example > .env

uv run uvicorn app.main:app --reload     # http://127.0.0.1:8000/docs
```

Google Calendar and Gemini are optional locally: with `CALENDAR_ENABLED=false` nothing is synced,
and without `GEMINI_API_KEY` the agent only sends its fallback reply. Keep real keys outside the
repo (for example `~/.secrets/booking-agent.env`). More: [docs/local-supabase.md](docs/local-supabase.md),
[docs/google-calendar.md](docs/google-calendar.md).

## Tests

```bash
uv run pytest                        # unit tests; database-backed tests skip without DATABASE_URL

export DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
REQUIRE_DB=1 uv run pytest           # everything, failing instead of skipping if config is missing
supabase test db                     # pgTAP: overlap constraint, RLS, privileges
uv run ruff check . && uv run ruff format --check .
```

Without a database, the last full local run was 560 passed and 75 skipped (the skipped ones need
Postgres or real Google or Gemini keys). CI runs lint, pgTAP, pytest with `REQUIRE_DB=1`,
`pip-audit`, and a Docker build with a `/health` smoke test and a non-root check. Real Google
Calendar and Gemini tests live in a manual workflow, `live.yml`, so they never gate a deploy
(run 36876498011 passed).

## Try the live API

Sign-ups are closed, so a demo user is created by hand. With the demo credentials in
`~/.secrets/booking-agent.env` (never committed):

```bash
uv run python scripts/get_demo_token.py            # prints only the access token (valid for 1 hour)
```

Open `/docs`, click **Authorize**, paste the token, and try `GET /services`, `GET /availability`,
`POST /chat` or `POST /bookings`. Use `--admin` for the admin demo user. Or with curl:

```bash
TOKEN=$(uv run python scripts/get_demo_token.py)
curl -s -H "Authorization: Bearer $TOKEN" https://ai-booking-agent-68b73.containers.snapdeploy.app/services
curl -s -X POST https://ai-booking-agent-68b73.containers.snapdeploy.app/chat \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"message": "I would like a haircut on Monday at 10am"}'
```

Requests from scripts need a normal `User-Agent` (see Engineering highlights); `curl` and
browsers are fine.

| Method and path | What it does |
|---|---|
| `GET /health` | Liveness (no auth) |
| `GET /services`, `GET /business-hours`, `GET /availability` | Catalogue and free slots |
| `POST /bookings`, `GET /bookings` | Create and list your own bookings |
| `PATCH /bookings/{id}`, `POST /bookings/{id}/cancel` | Reschedule or cancel your own booking |
| `POST /chat` | One turn with the booking agent |
| `GET /admin/bookings` | All bookings, admin only |

## Deployment

The Dockerfile (multi-stage, non-root, listens on `$PORT`, default 8080) is deployed to
SnapDeploy's free tier (512 MB, 0.25 vCPU, one container). Deploys are manual once CI is green on
`main`. The database is a hosted Supabase free project (Singapore) reached through the session
pooler with a pool of 5. One container is deliberate: the chat rate limiter is in memory. Runbook,
free-tier limits and the environment-variable trap: [docs/deploy.md](docs/deploy.md).

## Engineering highlights

Real problems found while building, each pinned by a test or a doc.

- **A booking race deadlocked instead of conflicting.** Six threads booking the same slot at once
  sometimes made Postgres deadlock rather than raise the exclusion-constraint error: 77 deadlocks
  over 5 runs, with runs taking 10 to 31 seconds. Nobody was ever double-booked, but a loser would
  have got a 500. Fix: an advisory lock serializes booking writes, plus one retry for a deadlock
  victim. After it: 0 deadlocks over 10 runs (80 rounds, 480 attempts) and about 0.35 s per run.
  [docs/concurrency.md](docs/concurrency.md)
- **`201 Created` before the commit.** FastAPI cleans up `yield` dependencies after the response
  by default, so a client could see success before the booking was committed. The connection
  dependency uses `scope="function"`, and a test asserts the order: handler, commit, response.
- **Google Calendar behaviour only a live test could show.** Against a real calendar, a deleted
  event is a `cancelled` tombstone: `patch` answers 200 and does not revive it, and creating the
  same id again answers 409, which looks the same as "the first try worked". The mocked tests had
  assumed 404 and "409 means created". The adapter now checks the event's status. 4 of 4 live
  tests pass and leave nothing behind. [docs/google-calendar.md](docs/google-calendar.md)
- **A secret in an error message.** In Phase 4 a settings `ValidationError` echoed the raw
  service-account JSON. The fix is `hide_input_in_errors=True` plus `SecretStr` on every
  credential. The regression test
  `tests/unit/test_config.py::test_enabled_calendar_rejects_a_key_that_is_not_service_account_json`
  fails if the setting is removed (checked), and passes with it.
- **SnapDeploy overrode production settings from `.env.example`.** After a deploy, `APP_ENV` went
  back to `development` (silently turning HSTS off) and `CALENDAR_ENABLED` to `false`. Fix: every
  value in `.env.example` is commented out, and the runbook checks both after each deploy.
- **Cloudflare blocked Python's default User-Agent.** The host sits behind Cloudflare, which
  answers 403 (error code 1010, not our error format) to `Python-urllib`. Scripts such as
  `scripts/get_demo_token.py` send a normal User-Agent.

## Docs

[agent.md](docs/agent.md), [chat-api.md](docs/chat-api.md), [concurrency.md](docs/concurrency.md),
[deploy.md](docs/deploy.md), [google-calendar.md](docs/google-calendar.md),
[local-supabase.md](docs/local-supabase.md), [security.md](docs/security.md),
and the phase-by-phase log in [tasks.md](tasks.md).
