# Tasks

Workflow: one phase at a time. At the end of each phase run tests, summarise, list changed files, and stop for review. One git commit per task. TDD is mandatory.

## Phase 0: Repo setup
- [x] git init, uv project, Ruff, pytest, pytest-cov (coverage tracked from first test)
- [x] Folder structure (layered architecture)
- [x] Health endpoint (test first) and config via pydantic-settings
- [x] `.env.example`
- [x] Dockerfile (multi-stage, non-root, honours `$PORT`). Not build-verified locally: Docker Desktop is not installed yet

## Phase 1: Database
Decisions (approved): authenticated users get SELECT only on their own rows; all writes go through the backend. Backend connects with a role that bypasses RLS, so every repository query MUST filter by user_id. `conversation_sessions` has RLS enabled with NO policies and no grants (Data API cannot read session state). One session row per user. Reschedule updates the same row and keeps the same `google_event_id`. `services` and `business_hours` readable by `authenticated` only.
- [x] Propose final SQL (approved in principle; final version shown for sign-off before files are written)
- [x] Verified on local stack (`supabase start`, `supabase test db`): overlap constraint, RLS via `SET ROLE authenticated`, anon/default privileges; tests proven to fail when the schema is broken
- [x] Docker image built and run locally (non-root, /health and /docs OK)
- [x] Revoked anon/authenticated grants on sequences and `set_updated_at()` (migration 07, pgTAP 005)
- Known, accepted: Supabase's `supabase_admin` default privileges still grant new tables to anon/authenticated. Not changed (Supabase-managed). Our migrations run as `postgres`, and pgTAP 001 (anon has no privileges on any public table) fails CI if any table is ever exposed this way.
- [x] Migrations: profiles, services, business_hours, bookings, conversation_sessions
- [x] Overlap-prevention constraint on confirmed bookings, RLS on user-owned tables
- [x] Seed services and business hours

## Phase 2: Auth
Decisions (approved): JWKS only (ES256/RS256, never HS256). Token `role` claim is never used for authorization. JWKS fetch has a timeout and cache and fails closed (503 plus an ERROR log line).
- [x] Validate Supabase JWT (`core/security.py`), JWKS key provider (`integrations/supabase_jwks.py`), `get_current_user` dependency (`api/deps.py`)
- [x] Tests: valid, expired, missing, malformed, wrong issuer/audience/signature, alg=none, HS256 key confusion, unknown kid, JWKS down/slow/garbage
- [x] Local asymmetric signing verified with a real GoTrue token (opt-in `pytest -m local_supabase`); setup in docs/local-supabase.md
- Follow-up (Phase 7): reject anonymous-sign-in tokens (`is_anonymous` claim) if anonymous sign-ins are ever enabled

## Phase 3: Core booking service (no AI)
Decisions (approved): `BUSINESS_TIMEZONE` default Asia/Karachi (DST proven with Europe/London tests); 15-minute slot grid; psycopg 3 sync + pool; thin routers in this phase; cancel is `POST /bookings/{id}/cancel` (row kept), reschedule is `PATCH /bookings/{id}`; only own, confirmed, future bookings can be changed; cancelled bookings return 409 `booking_not_active` on both cancel and reschedule.
- [x] Availability, create, reschedule, cancel, overlap prevention (full TDD)
- [x] Integration test: user A cannot read, reschedule or cancel user B's bookings (repositories always filter by user_id)
- [x] Test: rescheduling a booking to a time overlapping itself succeeds; to another booking's time fails (409)
- [x] Thin routers: GET /services, /business-hours, /availability; POST/GET /bookings; PATCH /bookings/{id}; POST /bookings/{id}/cancel; GET /admin/bookings
- [x] Concurrency: advisory lock + deadlock retry; race test (6 threads x 8 rounds) passes with 0 deadlocks; DB connection scope="function" so commits happen before the response
- Follow-up (Phase 4): Google Calendar sync hooks into create/reschedule/cancel and fills `google_event_id`
- Follow-up (Phase 7): 404/405 from unknown routes still use FastAPI's default body, not the standard error format

## Phase 4: Google Calendar integration
Decisions (approved): service account (calendar shared to it), no OAuth user flow. Strict consistency (a Google failure makes create, reschedule or cancel fail with 503 and roll back). Availability fails closed with 503. Event title `"<service> - <customer name>"`, booking id in description and private extended properties, no email or phone. All-day events block the whole day, read in `BUSINESS_TIMEZONE`.
- [x] `CalendarPort`, `GoogleCalendar` adapter (service account, `calendar.events` scope, timeout, backoff, deterministic event ids, fail closed), secret-safe settings
- [x] Live test against a real calendar: 4/4 pass, nothing left behind. It found two real-API behaviours (deleted events are `cancelled` tombstones: patch answers 200, re-create answers 409); the adapter handles both and the docs record them
- [x] Wired into `BookingService`: create, reschedule and cancel sync the event inside the request transaction (DB first, Google last); manual events block bookings and availability; `google_event_id` is the booking id hex, stored at insert
- [x] Tests: `singleEvents=true` (recurring expanded), `nextPageToken` paging, all-day dates read in the business timezone (Karachi, Los Angeles, a London DST day), Google failures roll back over HTTP against real Postgres
- [x] `FakeCalendar` for tests, `NullCalendar` when `CALENDAR_ENABLED=false`; startup builds the Google client (a bad key stops startup) or warns that sync is off
- Known limit: a failed database commit after Google accepted a create leaves an orphan event (carries the booking id in private properties)
- Follow-up (Phase 7): a reconciliation script for orphan events, if wanted

## Phase 5: LangGraph agent
Decisions (approved): conversation state lives in our own `conversation_sessions` table (no LangGraph checkpointer), so the confirmation gate is a stored proposal, not `interrupt()`. Gemini sits behind `LanguageModel` (default `gemini-3.5-flash-lite`, `GEMINI_MODEL`); any failure or a missing key gives a fixed fallback reply and no write. The gate covers create, reschedule and cancel: only a whole-message explicit yes on the next turn (deterministic, not the model) executes; a clear no clears the proposal and asks for another time. `user_id` always comes from the JWT; the model can only pick one of the user's own bookings by list number.
- [x] Nodes with tests: understand, plan, propose, confirm_gate, execute (`app/agents/`, docs/agent.md)
- [x] Confirmation gate tested explicitly (same-turn confirm, ambiguous reply, no, expiry, replaced proposal, slot taken, double yes, only edge into `execute`, calendar failure rolls back)
- [x] Prompt-injection tests: instruction override, booking for another user, cancelling another user's booking id or number, tampered stored proposals; all end with no write
- [x] Session state reset when a booking is completed, rescheduled or cancelled (tested in memory and on Postgres)
- [x] Gemini adapter (`google-genai`), safe fallback on every failure; live tests are opt-in (`pytest -m live_gemini`) and pass
- Known limitation: confirmation words are English only (documented)
- [x] Execute turns business outcomes (slot taken, booking cancelled meanwhile) into a friendly reply that clears the stale proposal and offers fresh slots; calendar and database errors still propagate (tested)
- Follow-up: Phase 6 requirements (one transaction per request, save the session only on success) are listed under Phase 6

## Phase 6: Chat API
Decisions (approved): per-user rate limit of 10 requests per 60 seconds, in memory and keyed by the JWT user id (not the IP), so it is per instance. `calendar_event_missing` stays a 409 whose message says to cancel and book again. One chat turn per user at a time via `pg_try_advisory_xact_lock` (non-blocking, two-key form, dedicated namespace constant), a second concurrent turn gets 409 `turn_in_progress`. The connection is held during the model call (documented trade-off).
- [x] POST /chat (load state, run graph, save state), in-memory rate limiting (`docs/chat-api.md`)
- [x] **One database transaction per chat request**: one connection per request, so a calendar or database error during the turn rolls back every write of that turn (proved on real Postgres)
- [x] **Save the session only when the turn succeeds**: a failed turn leaves the stored proposal in place, so the customer can say yes again; 503 in the standard error format (tested over HTTP)
- [x] Business outcomes at execute (slot taken, booking cancelled meanwhile) are normal 200 replies (tested over HTTP)
- [x] Input length validation at the API (1 to 1000 characters after trimming, extra fields rejected, message never echoed in errors)
- [x] Concurrent turns for the same user: second fails fast with 409 `turn_in_progress`, first is unharmed, other users are not blocked (tested; the test fails if the lock is removed)
- Known limit: the limiter is per instance and resets on restart (Phase 8 pins Cloud Run to one instance)

## Phase 7: Hardening
- [ ] Error handling, logging, input validation, OWASP API Top 10 review

## Phase 8: CI/CD
- [ ] GitHub Actions: lint and tests
- [ ] **CI must also run `supabase start` and `supabase test db`, not only pytest** (the pgTAP suite guards the overlap constraint, RLS and privileges)
- [ ] CI runs the live Google tests only if a test calendar and key are stored as CI secrets (otherwise they are skipped without `REQUIRE_DB`); `CALENDAR_ENABLED` must be `true` in the deployed service
- [ ] **CI sets `REQUIRE_DB=1`** plus `DATABASE_URL`, `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`, so missing configuration fails the DB-backed and real-GoTrue tests instead of skipping them (see docs/local-supabase.md)
- [ ] CI must create `supabase/signing_keys.json` before `supabase start` (`echo '[]' > ...` then `supabase gen signing-key --algorithm ES256 --append`; see docs/local-supabase.md) and run `pytest -m local_supabase` with `SUPABASE_URL` / `SUPABASE_PUBLISHABLE_KEY`
- [ ] Verify JWKS verification against a hosted Supabase project (asymmetric signing keys enabled) before relying on it in production
- [ ] **CI must build the Docker image so it is verified before deploy** (Docker was never built locally before this phase; install Docker Desktop first)
- [ ] **Cloud Run `--max-instances=1`**: the chat rate limiter is in memory and per instance (a user could exceed the limit across instances, and a restart resets it). Raise it only after moving the limiter to a shared store such as Redis (see docs/chat-api.md)
- [ ] Check current Cloud Run free tier limits, deploy, verify /docs live

## Phase 9: Portfolio polish
- [ ] README (diagrams, setup, env vars, API examples), sample curl requests, demo script
