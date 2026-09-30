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
- [x] Migrations: profiles, services, business_hours, bookings, conversation_sessions
- [x] Overlap-prevention constraint on confirmed bookings, RLS on user-owned tables
- [x] Seed services and business hours

## Phase 2: Auth
- [ ] Validate Supabase JWT, current-user dependency
- [ ] Tests: valid, expired, missing token

## Phase 3: Core booking service (no AI)
- [ ] Availability, create, reschedule, cancel, overlap prevention (full TDD)
- [ ] Integration test: user A cannot read user B's bookings (repositories always filter by user_id)
- [ ] Test: rescheduling a booking to a time overlapping itself succeeds; to another booking's time fails (409)

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
- [ ] **CI must build the Docker image so it is verified before deploy** (Docker was never built locally before this phase; install Docker Desktop first)
- [ ] Check current Cloud Run free tier limits, deploy, verify /docs live

## Phase 9: Portfolio polish
- [ ] README (diagrams, setup, env vars, API examples), sample curl requests, demo script
