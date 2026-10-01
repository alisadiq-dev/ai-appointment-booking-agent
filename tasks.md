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
- [x] (Phase 7) Anonymous-sign-in tokens (`is_anonymous` claim) are rejected

## Phase 3: Core booking service (no AI)
Decisions (approved): `BUSINESS_TIMEZONE` default Asia/Karachi (DST proven with Europe/London tests); 15-minute slot grid; psycopg 3 sync + pool; thin routers in this phase; cancel is `POST /bookings/{id}/cancel` (row kept), reschedule is `PATCH /bookings/{id}`; only own, confirmed, future bookings can be changed; cancelled bookings return 409 `booking_not_active` on both cancel and reschedule.
- [x] Availability, create, reschedule, cancel, overlap prevention (full TDD)
- [x] Integration test: user A cannot read, reschedule or cancel user B's bookings (repositories always filter by user_id)
- [x] Test: rescheduling a booking to a time overlapping itself succeeds; to another booking's time fails (409)
- [x] Thin routers: GET /services, /business-hours, /availability; POST/GET /bookings; PATCH /bookings/{id}; POST /bookings/{id}/cancel; GET /admin/bookings
- [x] Concurrency: advisory lock + deadlock retry; race test (6 threads x 8 rounds) passes with 0 deadlocks; DB connection scope="function" so commits happen before the response
- Follow-up (Phase 4): Google Calendar sync hooks into create/reschedule/cancel and fills `google_event_id`
- [x] (Phase 7) 404/405 now use the standard error format

## Phase 4: Google Calendar integration
Decisions (approved): service account (calendar shared to it), no OAuth user flow. Strict consistency (a Google failure makes create, reschedule or cancel fail with 503 and roll back). Availability fails closed with 503. Event title `"<service> - <customer name>"`, booking id in description and private extended properties, no email or phone. All-day events block the whole day, read in `BUSINESS_TIMEZONE`.
- [x] `CalendarPort`, `GoogleCalendar` adapter (service account, `calendar.events` scope, timeout, backoff, deterministic event ids, fail closed), secret-safe settings
- [x] Live test against a real calendar: 4/4 pass, nothing left behind. It found two real-API behaviours (deleted events are `cancelled` tombstones: patch answers 200, re-create answers 409); the adapter handles both and the docs record them
- [x] Wired into `BookingService`: create, reschedule and cancel sync the event inside the request transaction (DB first, Google last); manual events block bookings and availability; `google_event_id` is the booking id hex, stored at insert
- [x] Tests: `singleEvents=true` (recurring expanded), `nextPageToken` paging, all-day dates read in the business timezone (Karachi, Los Angeles, a London DST day), Google failures roll back over HTTP against real Postgres
- [x] `FakeCalendar` for tests, `NullCalendar` when `CALENDAR_ENABLED=false`; startup builds the Google client (a bad key stops startup) or warns that sync is off
- Known limit: a failed database commit after Google accepted a create leaves an orphan event (carries the booking id in private properties)
- Decided in Phase 7: no reconciliation script (documented as a known limit in docs/security.md)

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
Decisions (approved): per-user rate limit of 10 requests per 60 seconds, in memory and keyed by the JWT user id (not the IP), so it is per instance. `calendar_event_missing` stays a 409 whose message (in chat) spells out the recovery: reply 'no', then ask to cancel and book again. One chat turn per user at a time via `pg_try_advisory_xact_lock` (non-blocking, two-key form, dedicated namespace constant), a second concurrent turn gets 409 `turn_in_progress`. The connection is held during the model call (documented trade-off).
- [x] POST /chat (load state, run graph, save state), in-memory rate limiting (`docs/chat-api.md`)
- [x] **One database transaction per chat request**: one connection per request, so a calendar or database error during the turn rolls back every write of that turn (proved on real Postgres)
- [x] **Save the session only when the turn succeeds**: a failed turn leaves the stored proposal in place, so the customer can say yes again; 503 in the standard error format (tested over HTTP)
- [x] Business outcomes at execute (slot taken, booking cancelled meanwhile) are normal 200 replies (tested over HTTP)
- [x] Input length validation at the API (1 to 1000 characters after trimming, extra fields rejected, message never echoed in errors)
- [x] Concurrent turns for the same user: second fails fast with 409 `turn_in_progress`, first is unharmed, other users are not blocked (tested; the test fails if the lock is removed)
- Known limit: the limiter is per instance and resets on restart (Phase 8 deploys to Render's free plan, which runs exactly one instance)
- Known limit: a request with an invalid body (422) still counts against the rate limit and briefly takes a pooled connection and the user's turn lock (FastAPI resolves dependencies first); accepted and documented in docs/chat-api.md

## Phase 7: Hardening
Decisions (approved): `/docs` stays enabled; no CORS (a future frontend needs CORS with explicit allowed origins, never `*`); no orphan-event reconciliation (known limit); admin rights come only from `profiles.role` in the database, never from a token claim.
- [x] Standard error format for 404, 405, 413 and unhandled 500 (generic message, stack trace only in the log)
- [x] Reject `is_anonymous` tokens (fail closed on any value other than absent or `false`)
- [x] Test: a token with admin-looking claims but a normal profile gets 403 on `GET /admin/bookings` (checked to fail when the service check is weakened)
- [x] Structured JSON logging, `X-Request-ID` on every response and log line, one access line per request (route template, status, duration)
- [x] Tests: the Authorization header and chat message text never appear in log output (checked to fail when the header is deliberately logged)
- [x] Security headers on every response; 32 KiB request body cap (413)
- [x] Input review: found and fixed a 500 for start times at the edge of the calendar (year 1 or 9999), now 422; response fields and unknown request fields tested
- [x] OWASP API Security Top 10 (2023) review in docs/security.md (risk, what we do, evidence, known limits)
- [x] Cap on active future bookings per user (`MAX_ACTIVE_BOOKINGS_PER_USER`, default 3) in the service layer, atomic in the booking insert: 409 `booking_limit_reached` on REST, a friendly reply with no write in chat; rescheduling is not a new booking (tested on fakes, HTTP, chat and real Postgres incl. a concurrency test)
- Known limits (listed in docs/security.md): only `/chat` is rate limited (no limit on book/cancel cycles), `GET /bookings` is not paginated, no HSTS or CSP, no `/v1` prefix, no automated dependency scan (Phase 8)

## Phase 8: CI/CD and deployment
Decisions (approved): deploy to Render's free web service from the existing Dockerfile (no GCP billing account available), region Singapore, auto-deploy only after CI passes (`autoDeployTrigger: checksPass`). Production database is a hosted Supabase free project (Singapore), reached through the **session pooler** (IPv4, port 5432); production sets `DB_POOL_MAX_SIZE=5` so the free pooler is not exhausted. Production migrations run only from a manual workflow. No keep-alive ping: the 15-minute idle cold start is documented instead. Secrets live only in Render environment variables and GitHub repository secrets.
- [x] GitHub Actions: lint and tests; add a dependency vulnerability scan (`pip-audit` on an exported `uv.lock`) and Dependabot for uv, github-actions and docker (from docs/security.md, API9)
- [x] **CI must also run `supabase start` and `supabase test db`, not only pytest** (the pgTAP suite guards the overlap constraint, RLS and privileges)
- Split decision: PR #8 delivers CI, `render.yaml` and the migrations workflow. The unticked items below (live.yml run, hosted token check, HSTS, deploy verification) follow in a second PR after the deploy.
- [ ] Live Google and Gemini tests run only from a manual workflow (`live.yml`, `workflow_dispatch`, needs the CI secrets, fails if one is missing) so they never gate Render or spend quota on every push; `CALENDAR_ENABLED` must be `true` in the deployed service
- [x] **CI sets `REQUIRE_DB=1`** plus `DATABASE_URL`, `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`, so missing configuration fails the DB-backed and real-GoTrue tests instead of skipping them (see docs/local-supabase.md)
- [x] CI must create `supabase/signing_keys.json` before `supabase start` (`echo '[]' > ...` then `supabase gen signing-key --algorithm ES256 --append`; see docs/local-supabase.md) and run `pytest -m local_supabase` with `SUPABASE_URL` / `SUPABASE_PUBLISHABLE_KEY`
- [x] **CI must build the Docker image and smoke-test `/health`, so it is verified before deploy**
- [x] `render.yaml` blueprint: Docker runtime, free plan, Singapore, one instance, health check `/health`, `autoDeployTrigger: checksPass`, `CALENDAR_ENABLED=true`, `DB_POOL_MAX_SIZE=5`, all secrets `sync: false`
- [x] Manual workflow (`workflow_dispatch`) that runs `supabase link` and `supabase db push` against the hosted project
- [x] Hosted Supabase project `dhovoboznckrknphwpir` (Singapore) created: Data API off, ES256 signing key active and served at the JWKS URL, anonymous sign-ins off, sign-ups closed, confirm email on
- [ ] Verify a real hosted user token against the JWKS (after migrations and a hand-made demo user)
- [ ] **Render free plan runs one instance**, which satisfies the in-memory chat rate limiter; document that, and that a restart or spin-down resets the counters. Raise the instance count only after moving the limiter to a shared store such as Redis (see docs/chat-api.md)
- [ ] Document the cold start (spin-down after 15 idle minutes, about a minute to restart) and the free-plan limits (750 instance hours a month, ephemeral filesystem)
- [ ] Add an HSTS header (`Strict-Transport-Security`) once HTTPS is confirmed on the Render URL (set in the app's security headers; start with a short max-age)
- [ ] Deploy, verify /health and /docs live, run one real chat turn against production

## Phase 9: Portfolio polish
- [ ] README (diagrams, setup, env vars, API examples), sample curl requests, demo script
- [ ] Demo token script: password grant for a hand-made demo user (sign-ups are closed in production), credentials from a local secrets file, prints only the access token (see docs/deploy.md)
