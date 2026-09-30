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
- [ ] Interface plus implementation: sync create, update, delete. Mocked in tests

## Phase 5: LangGraph agent
- [ ] Nodes one by one with tests; confirmation gate tested explicitly
- [ ] Reset the user's session state when a booking is completed or cancelled, so the next chat starts fresh (test it)

## Phase 6: Chat API
- [ ] POST /chat (load state, run graph, save state), in-memory rate limiting

## Phase 7: Hardening
- [ ] Error handling, logging, input validation, OWASP API Top 10 review

## Phase 8: CI/CD
- [ ] GitHub Actions: lint and tests
- [ ] **CI must also run `supabase start` and `supabase test db`, not only pytest** (the pgTAP suite guards the overlap constraint, RLS and privileges)
- [ ] **CI sets `REQUIRE_DB=1`** plus `DATABASE_URL`, `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`, so missing configuration fails the DB-backed and real-GoTrue tests instead of skipping them (see docs/local-supabase.md)
- [ ] CI must create `supabase/signing_keys.json` before `supabase start` (`echo '[]' > ...` then `supabase gen signing-key --algorithm ES256 --append`; see docs/local-supabase.md) and run `pytest -m local_supabase` with `SUPABASE_URL` / `SUPABASE_PUBLISHABLE_KEY`
- [ ] Verify JWKS verification against a hosted Supabase project (asymmetric signing keys enabled) before relying on it in production
- [ ] **CI must build the Docker image so it is verified before deploy** (Docker was never built locally before this phase; install Docker Desktop first)
- [ ] Check current Cloud Run free tier limits, deploy, verify /docs live

## Phase 9: Portfolio polish
- [ ] README (diagrams, setup, env vars, API examples), sample curl requests, demo script
