# Tasks

Workflow: one phase at a time. At the end of each phase run tests, summarise, list changed files, and stop for review. One git commit per task. TDD is mandatory.

## Phase 0: Repo setup
- [x] git init, uv project, Ruff, pytest, pytest-cov (coverage tracked from first test)
- [x] Folder structure (layered architecture)
- [ ] Health endpoint (test first) and config via pydantic-settings
- [ ] `.env.example`
- [ ] Dockerfile (multi-stage, non-root, honours `$PORT`). Not build-verified locally: Docker Desktop is not installed yet

## Phase 1: Database
- [ ] Propose final SQL and wait for approval
- [ ] Migrations: profiles, services, business_hours, bookings, conversation_sessions
- [ ] Overlap-prevention constraint on confirmed bookings, RLS on user-owned tables
- [ ] Seed services and business hours

## Phase 2: Auth
- [ ] Validate Supabase JWT, current-user dependency
- [ ] Tests: valid, expired, missing token

## Phase 3: Core booking service (no AI)
- [ ] Availability, create, reschedule, cancel, overlap prevention (full TDD)

## Phase 4: Google Calendar integration
- [ ] Interface plus implementation: sync create, update, delete. Mocked in tests

## Phase 5: LangGraph agent
- [ ] Nodes one by one with tests; confirmation gate tested explicitly

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
