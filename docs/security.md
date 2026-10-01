# Security review: OWASP API Security Top 10 (2023)

Each item has the risk, what this API does, and the evidence (a test or a pgTAP file you can run).
Where something is only partly covered it says so under **Gap / known limit**. Test paths are
relative to `tests/`; `pgTAP` files are in `supabase/tests/database/` and run with
`supabase test db`.

Trust model in one paragraph: the browser or app talks only to this backend. Identity comes from a
Supabase JWT verified against the project's JWKS. The backend connects to Postgres with a role that
bypasses RLS, so **every repository query filters by `user_id` itself**; RLS and grants are a second
line of defence for the Data API, not the first. In production the Data API is **switched off**
(see API8), so these are defence in depth there; they stay essential on the local stack and for
any future project that enables it. Admin rights come only from `profiles.role` in the
database, never from a token claim.

## API1: Broken object level authorization

**Risk.** A user reads or changes another user's booking by guessing or reusing an id.

**What we do.**
- Every booking query takes the caller's `user_id` from the verified token and filters by it
  (`get_for_user`, `list_for_user`, `update_times`, `cancel` all include `and user_id = %s`).
- Someone else's booking is indistinguishable from a missing one: both answer `404
  booking_not_found`, so ids cannot be probed.
- Ids are random UUIDs, not sequential numbers.
- The chat agent cannot name a booking id at all: the model only picks a list number from the
  caller's own bookings, and the stored proposal is re-checked against the caller at execute time.
- `user_id` is never accepted from a request body (extra fields are rejected, see API3).

**Evidence.**
`integration/test_booking_service_db.py::test_user_a_cannot_read_user_b_bookings` and
`::test_user_b_cannot_reschedule_or_cancel_user_a_booking` (real Postgres);
`integration/test_repositories.py::test_get_for_user_returns_only_the_owners_booking`,
`::test_update_times_cannot_touch_another_users_booking`,
`::test_cancel_cannot_touch_another_users_booking`;
`integration/test_api_bookings.py::test_reschedule_someone_elses_booking_is_404_not_403`,
`::test_cancel_someone_elses_booking_is_404`;
agent: `unit/agents/test_agent_injection.py` (cancelling or rescheduling another user's booking by
id or number, tampered stored proposals) and
`integration/test_api_chat_db.py::test_one_user_cannot_confirm_another_users_proposal`;
database layer: `004_rls.sql` (user 1 sees only their own bookings and cannot read user 2's).

**Gap / known limit.** The backend role bypasses RLS, so a future query that forgets the
`user_id` filter would not be stopped by the database. The repository tests above are the guard;
review new repository methods for it.

## API2: Broken authentication

**Risk.** Forged, stolen or wrongly accepted tokens.

**What we do.**
- Supabase JWTs are verified with **asymmetric keys only** (ES256 and RS256) from the project's
  JWKS. HS256 and `alg=none` are never accepted, so the "use the public key as an HMAC secret"
  attack fails.
- Required claims: `exp`, `iss`, `aud`, `sub`; issuer and audience are checked; 10 seconds of clock
  leeway; `sub` must be a UUID.
- **Anonymous sign-in tokens (`is_anonymous`) are rejected.** They are validly signed but are not
  customers. Anything other than an absent claim or a literal `false` is rejected (fail closed).
- If the JWKS cannot be fetched, requests fail closed with 503, never "allow".
- Errors are generic (`401 unauthorized`); the reason goes to the log only, never to the caller.
  The token itself is never logged (see API8).
- The `role` claim in the token is never used for authorization.

**Evidence.** `unit/test_security.py` (expired, missing `exp`, not yet valid, wrong issuer or
audience, missing claim, non-UUID `sub`, wrong key, `alg=none`, HS256 key confusion, malformed,
key-lookup failure, JWKS down, generic errors, **`test_anonymous_sign_in_token_is_rejected`** and
its variants); `integration/test_auth_dependency.py` (HTTP level, JWKS down, slow, garbage);
`integration/test_api_bookings.py::test_every_endpoint_requires_authentication`;
`integration/test_auth_local_supabase.py` (opt-in, a real GoTrue token).

**Gap / known limit.** There is no brute-force protection on login: login is Supabase's, not ours.
Configure Supabase Auth rate limits and keep anonymous sign-ins disabled in the project. We have
not yet verified JWKS against a hosted project (tracked in tasks.md, Phase 8).

## API3: Broken object property level authorization

**Risk.** A client sets a property it must not control (mass assignment, such as `user_id`,
`status`, `role`), or the API returns properties it should not (excessive data exposure).

**What we do.**
- Request models use `extra="forbid"`: unknown fields are a `422`, not silently ignored. The only
  fields a client can send are the ones listed (`service_id`, `start_at`, `message`).
- Response models are explicit allow-lists. Customers get `id`, `service_id`, `start_at`, `end_at`,
  `status`; `user_id` appears only on the admin listing; `google_event_id` is never returned.
- Calendar events carry no email or phone number (see [google-calendar.md](google-calendar.md)).
- A user cannot change their own role: the `authenticated` role has **no write privilege on any
  public table**, so `profiles.role` can only be changed by the backend or an operator.

**Evidence.**
`integration/test_hardening_http.py::test_unknown_body_fields_are_refused_not_ignored` and
`::test_customer_booking_responses_expose_only_the_public_fields`;
`integration/test_api_chat.py` (`{"message": "hi", "role": "admin"}` is rejected);
role self-change: pgTAP `002_default_privileges.sql` ("authenticated has no write privileges on
any public table"), `004_rls.sql` ("user 1 cannot insert, update or delete bookings directly",
profiles are select-only for `authenticated`) and `005_sequences_and_functions.sql`.

## API4: Unrestricted resource consumption

**Risk.** Cost or availability attacks: floods, huge bodies, expensive calls (the model, Google).

**What we do.**
- `POST /chat` (the only route that spends model and Google calls) is rate limited **per user id**,
  10 requests per 60 seconds, after authentication and before the database or the model. Details in
  [chat-api.md](chat-api.md).
- One chat turn per user at a time (`turn_in_progress` 409), so a user cannot stack slow model calls.
- Request body cap of **32 KiB**, enforced in the ASGI layer, so a huge body is refused with `413
  payload_too_large` before it is parsed (also for chunked bodies with no `Content-Length`).
- Chat message at most 1000 characters; `limit` at most 200 and `offset >= 0` on the admin list.
- Timeouts on every outbound call (JWKS, Google Calendar, Gemini) and a bounded database pool
  that fails with 503 when exhausted.
- Edge-of-calendar times (year 1 or 9999) used to overflow and return a 500; they are now a clean
  422 (found by a test in this phase).

**Evidence.** `unit/test_rate_limit.py`, `integration/test_api_chat.py` (429 with `Retry-After`,
per-user budgets, limit checked before the database), `integration/test_api_chat_db.py::test_a_second_simultaneous_turn_for_the_same_user_fails_fast_with_409`,
`integration/test_hardening_http.py` (413 for large and chunked bodies, extreme dates and times),
`integration/test_api_bookings.py::test_admin_pagination_is_validated`.

**Gap / known limit.** The rate limiter is in memory, so it is **per instance** and resets on a
restart; the Render free plan runs exactly one instance (see [deploy.md](deploy.md)). Only `/chat` is rate limited: the booking routes are
cheap and authenticated but not limited per user. `GET /bookings` returns all of the caller's own
bookings without pagination. The app caps request bodies at 32 KiB itself. Render's free plan cannot scale past one instance,
so there is no runaway scaling cost to alert on.

## API5: Broken function level authorization

**Risk.** A normal user calls an admin function, or an admin function is reachable by guessing.

**What we do.**
- The only privileged route is `GET /admin/bookings`. The service layer checks
  `profiles.role == 'admin'` **in the database** for the caller's own id and answers `403
  forbidden` otherwise. The same rule exists in Postgres as `private.is_admin()` for RLS.
- Admin rights never come from the token. Claims such as `role: admin`, `role: service_role`,
  `is_admin`, `app_metadata.role` or `user_metadata.role` are ignored.
- A user with no profile row is not an admin.
- No admin write endpoints exist.

**Evidence.** `integration/test_admin_authz.py` signs real tokens carrying admin-looking claims for
a normal user and expects `403` on `GET /admin/bookings` (and was checked to fail when the service
check is weakened); `integration/test_api_bookings.py::test_customers_cannot_use_the_admin_endpoint`;
`unit/test_security.py::test_token_role_claim_is_ignored`; pgTAP `004_rls.sql` (`private.is_admin()`
false for a customer, true for an admin, not callable by `anon`).

## API6: Unrestricted access to sensitive business flows

**Risk.** Abuse of a legitimate flow at scale: hoarding slots, spamming bookings, abusing the bot.

**What we do.**
- The booking flow cannot double book: a database overlap constraint plus an advisory lock
  serialise writes (see [concurrency.md](concurrency.md)).
- **Each user can hold at most `MAX_ACTIVE_BOOKINGS_PER_USER` active bookings (default 3).** Active
  means confirmed and not yet started. The rule is in the service layer, so REST and chat share it:
  a new booking over the limit is `409 booking_limit_reached` in the standard format. In chat the
  customer gets a friendly reply ("... Nothing has been changed.") and nothing is written; the agent
  says so before asking for details, and again at execute time in case the limit was reached
  between the proposal and the yes. The count is made **inside the booking insert, under the booking
  write lock**, so simultaneous requests from one user cannot both pass it.
- **Rescheduling is not a new booking** and is never blocked by the limit; cancelling frees a place.
  Cancelled bookings and bookings that have already started do not count. Other users' bookings
  are never counted.
- Only own, confirmed, future bookings can be changed; the chat agent writes only after an
  **explicit yes** on a later turn, read deterministically (not by the model).
- Chat is rate limited per user (API4). All writes need a verified, non-anonymous account.

**Evidence.** Limit: `unit/test_booking_service.py` ("active booking limit" section: up to the limit,
409 and no write or calendar event over it, per user, cancel frees a place, cancelled and started
bookings do not count, reschedule at the limit works, configurable, default 3);
`integration/test_api_bookings.py::test_the_fourth_active_booking_is_409_in_the_standard_format`
and `::test_the_limit_does_not_affect_other_users_or_rescheduling`;
`unit/agents/test_booking_limit.py` (friendly reply and no write, a later yes books nothing, limit
reached between proposal and yes, reschedule still allowed);
real Postgres: `integration/test_booking_service_db.py::test_concurrent_bookings_by_one_user_cannot_exceed_the_limit`
(8 simultaneous bookings by one user give exactly 3; checked to fail without the SQL check) and
`::test_the_limit_is_enforced_in_sql_and_reschedule_and_cancel_still_work`.
Overlap and gate: pgTAP `003_bookings_overlap.sql`; the 6-thread race test in
`integration/test_booking_service_db.py`; `unit/agents/test_confirmation_gate.py`;
`unit/agents/test_agent_injection.py`.

**Gap / known limit.** The limit bounds how many slots one account can hold, not how many accounts
exist: someone could still make many accounts, so sign-up protection (email confirmation, CAPTCHA,
Supabase Auth rate limits) matters and is configured in Supabase, not here. There is no limit on
how often a user can book and cancel in a row (only `/chat` is rate limited).

## API7: Server side request forgery

**Risk.** The server fetches a URL chosen by an attacker.

**What we do.** No endpoint takes a URL or host from the client. The three outbound calls go to
fixed destinations: the JWKS URL is derived from the `SUPABASE_URL` setting, and Google Calendar
and Gemini use their official client libraries with fixed hosts. The model's output is parsed into
a fixed schema (`Interpretation`) and never used to build a request target.

**Evidence.** `unit/agents/test_model_interface.py` (output is schema-bound);
`integration/test_jwks_key_provider.py`. This is by construction: there is no URL input to test.

**Gap / known limit.** The JWKS `kid` and `iss` come from the token header and claims, but only
the configured JWKS URL is ever fetched, and the issuer is compared to the configured value.

## API8: Security misconfiguration

**Risk.** Weak defaults: verbose errors, missing headers, exposed internals, leaked secrets,
permissive CORS.

**What we do.**
- **One error format** for every failure: `{"error": {"code", "message"}}`, including `404`, `405`,
  `413`, `422` and unhandled `500`. A 500 returns a generic message; the stack trace goes to the log.
  Validation errors list field names and error types only, never the submitted values.
- **Security headers** on every response, including errors: `X-Content-Type-Options: nosniff`,
  `Cache-Control: no-store` (responses are per user), `Referrer-Policy: no-referrer`,
  `X-Frame-Options: DENY`.
- **CORS is not enabled.** There is no browser frontend calling the API directly, so browsers
  block cross-origin calls, which is the safe default. **A future frontend needs CORS with an
  explicit list of allowed origins, never `"*"`**, and never `allow_credentials` together with a
  wildcard.
- **Structured JSON logs** with a request id (below). Secrets are `SecretStr` and never appear in
  reprs, logs or settings validation errors (`hide_input_in_errors`).
- **`/docs`, `/redoc` and `/openapi.json` stay enabled** (a deliberate decision: the API is
  authenticated and the schema holds no secrets; the portfolio demo needs them). Turn them off
  with `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)` if that changes.
- The container runs as a non-root user; Supabase and database roles follow least privilege
  (`anon` has no privileges on any public table).
- **Production Supabase project (`dhovoboznckrknphwpir`, Singapore) was created with the Data API
  disabled, "Automatically expose new tables" off and automatic RLS off.** The app never calls
  the Data API (PostgREST): it talks to Postgres directly through the backend role and uses Supabase
  only for Auth (JWKS). With the Data API off, `anon` and `authenticated` tokens cannot read or
  write any table over HTTP at all; RLS and grants remain as a second layer. Automatic RLS stays
  off because our own migrations enable RLS table by table and pgTAP checks it; the local stack
  keeps the Data API on, so the pgTAP privilege tests still run against the riskier configuration.
  If the Data API is ever enabled, re-read `supabase/tests/database` results against production
  first.
- Production must run with `CALENDAR_ENABLED=true` (a warning is logged at startup otherwise).

**Logging rules.** One access line per request with method, **route template** (not the raw
path), status and duration. The log never contains the `Authorization` header, query strings, raw
paths, request or response bodies, or chat text. Each request has an `X-Request-ID`: a valid
inbound id (8 to 64 of `A-Za-z0-9._-`) is reused, anything else is replaced by a UUID, and it is
returned on every response (also errors) and put on every log line of that request.

**Evidence.** `integration/test_error_format.py`; `integration/test_hardening_http.py` (headers on
200, 404 and 500; docs still work); `integration/test_request_logging.py` (request id rules,
access line, **`test_authorization_header_never_appears_in_the_logs_*`**,
**`test_chat_message_text_never_appears_in_the_logs`**, also for a 422 and a failing turn; these
were checked to fail when the header is deliberately logged); `unit/test_logging.py` (one JSON
object per line, newline forging); `unit/test_config.py`; pgTAP `001_anon_has_no_privileges.sql`.

**Gap / known limit.** No HSTS header yet: TLS is terminated by Render in front of the app. Phase 8 adds it
once HTTPS is confirmed on the live URL. No Content-Security-Policy (the API serves JSON, and the Swagger UI
page loads assets from a CDN, so a strict policy would break `/docs`).

## API9: Improper inventory management

**Risk.** Forgotten old versions, debug endpoints or undocumented routes stay reachable.

**What we do.** One version, one deployment. All routes are declared in code and listed in the
OpenAPI document (`/openapi.json`); there are no debug or test endpoints in the app (the `_probe`
and `_boom` routes exist only inside tests). The list of routes is: `/health`, `/services`,
`/business-hours`, `/availability`, `/bookings` (+ `/{id}`, `/{id}/cancel`), `/admin/bookings`,
`/chat`. Dependencies are pinned in `uv.lock`.

**Evidence.** `integration/test_api_bookings.py::test_every_endpoint_requires_authentication`
(each listed route demands a token; `/health` is the only public route).

**Gap / known limit.** There is no automated dependency vulnerability scan yet; add one (for
example `pip-audit` and Dependabot) with the CI work in Phase 8. There is no API versioning
prefix; add `/v1` before the first external client depends on the routes.

## API10: Unsafe consumption of APIs

**Risk.** Trusting data from third parties (Google, Gemini, Supabase) as if it were safe.

**What we do.**
- **Gemini output is untrusted.** It is parsed into a strict schema; it can only pick a service
  from the real service list and a booking by list number from the caller's own bookings; it can
  never carry a `user_id`; it cannot confirm anything (confirmation is a deterministic check of the
  next user message). A failure or garbage gives a fixed fallback reply and no write.
- **Google Calendar** calls have a timeout and bounded retries, and fail closed (503 and rollback)
  instead of booking without a synced event. Calendar data used to block time is read as times
  only; event titles from manual events are never shown to customers.
- **JWKS** responses are validated; a garbage or slow endpoint is a 503, never an accept.
- SQL uses bound parameters everywhere (psycopg `%s`), so model or user text cannot change a query.

**Evidence.** `unit/agents/test_agent_injection.py`, `unit/agents/test_model_interface.py`,
`unit/agents/test_gemini_model.py` (every failure gives the fallback), `unit/test_google_calendar.py`,
`integration/test_google_calendar_live.py` (opt-in), `integration/test_jwks_key_provider.py`
(down, slow, garbage), `integration/test_api_chat_db.py::test_a_calendar_failure_rolls_back_the_turn_and_keeps_the_proposal`.

**Gap / known limit.** Prompt injection cannot be fully prevented, only contained: the design
guarantees that a fooled model cannot write (explicit confirmation, own bookings only, server-side
validation), but it can still produce a silly reply. Confirmation words are English only (see
[agent.md](agent.md)).

## Other known limits

- **Orphan calendar events.** If the database commit fails after Google accepted a create, an
  orphan event remains. It carries the booking id in its private properties. A reconciliation script
  was considered and **deliberately not built** (rare, and it would add live Google side effects).
- The per-instance chat limiter and the single-instance Render free plan (see deploy.md).
- Supabase's own default privileges for `supabase_admin` are not ours to change; pgTAP `001`
  fails CI if a table is ever exposed that way.
