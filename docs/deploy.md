# Deployment (SnapDeploy free container + hosted Supabase)

The API runs as a Docker container on **SnapDeploy's free tier**, built from the repo's
`Dockerfile`. Live URL: <https://ai-booking-agent-68b73.containers.snapdeploy.app> (note `.app`).
The database and auth are a **hosted Supabase free project** (Singapore).

**Why not Render or Cloud Run.** The plan was GCP Cloud Run, then Render's free web service.
Cloud Run needed a billing account and Render asked for a card even for the free web service.
SnapDeploy is free with no card. (`render.yaml` was removed; it was never used.)

**Deploys are manual.** Auto Deploy on Push is **off**. After CI is green on `main`, open the
SnapDeploy dashboard and deploy from there. Nothing deploys by itself, so a red or missing CI
run can never reach production unless someone deploys it by hand: check the green tick first.

## What the free tier means for this app

| Limit (SnapDeploy free) | Effect here |
|---|---|
| **Small container: 512 MB, 0.25 vCPU**, one container | One process, so the in-memory chat rate limiter ([chat-api.md](chat-api.md)) has one set of counters. Do not run a second container without a shared limiter (for example Redis). |
| **Sleeps after 15 idle minutes** | The next request is a **cold start**: the first `/health` after sleep took about 29 seconds (2026-10-01), so clients should expect a slow first call. We deliberately run no keep-alive ping, because it would use up the free hours. |
| **100 free hours** | Check in the dashboard how they are counted and when they reset; a sleeping container should not use them. |
| Restart or sleep | All in-memory state is lost: the rate-limit counters reset. Bookings and chat sessions are safe, they live in Postgres. |
| Port 8080 | Set in the dashboard; the Dockerfile `EXPOSE`s 8080 and listens on `$PORT`, defaulting to 8080. |
| No shell access assumed | The app writes nothing to disk. Logs go to stdout as JSON (SnapDeploy's log view). |

The small CPU share is also why the 15-minute wake-up is slow: the container starts Python, builds
the Google client and opens the database pool before it answers.

## Database connection: session pooler, pool size 5

Supabase's direct host (`db.<ref>.supabase.co`) is IPv6 only, which free container hosts often
cannot reach, so the app uses the IPv4 **Session pooler**: use the string string from the Supabase dashboard's *Connect* dialog: host `aws-<n>-<region>.pooler.supabase.com`,
port **5432**, user `postgres.<project-ref>`. Do not build the host by hand, copy it.
Append `?sslmode=require`.

Set **`DB_POOL_MAX_SIZE=5`** in production (the code default is 10). Numbers confirmed in the
dashboard for this project (Nano compute, shared pooler `aws-0-ap-southeast-1.pooler.supabase.com`,
port 5432): **pool size 15** per user and database, **max client connections 200** (fixed on
Nano). In session mode every app connection holds one backend connection for as long as it lives,
so the real ceiling is the 15, not the 200.

- Steady state: at most 5 connections.
- Deploy overlap (old and new instance both up for a moment): at most 10.
- That leaves 5 of the 15 for the migrations workflow (`supabase db push`) and the dashboard.

With the default of 10, an overlapping deploy could take all 15 and lock out migrations and the
dashboard. Five is enough for the app because one chat request holds one connection for a whole
turn, and the per-user turn lock and rate limit bound the concurrency. If the compute size changes,
re-check the pool size before raising `DB_POOL_MAX_SIZE`.

## Environment variables

Not secret. Entered in the SnapDeploy dashboard:

| Variable | Value |
|---|---|
| `APP_ENV` | `production` |
| `LOG_LEVEL` | `INFO` |
| `SUPABASE_URL` | `https://dhovoboznckrknphwpir.supabase.co` |
| `BUSINESS_TIMEZONE` | `Asia/Karachi` |
| `DB_POOL_MAX_SIZE` | `5` |
| `CALENDAR_ENABLED` | **`true`** (the app refuses to start without the two Google values) |

Secrets, also entered in the dashboard only (never in git):

| Variable | What it is |
|---|---|
| `DATABASE_URL` | Session pooler string with `?sslmode=require` (see above) |
| `GOOGLE_CALENDAR_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON` | see [google-calendar.md](google-calendar.md) |
| `GEMINI_API_KEY` | Google AI Studio key (without it the agent only sends the fallback reply) |

Port 8080 is set in the dashboard; the Dockerfile also defaults to 8080.

The set is 10 variables in total (6 plus 4 secrets).

## First deploy, in order

1. **Migrations**: Actions tab, **Production migrations**, Run workflow with `dry_run` ticked,
   check the list (7 migrations), then again with `dry_run` **unticked** and `include_seed`
   **ticked** (the seed is repeat safe). Done 2026-10-01, verified read-only afterwards: 5 public
   tables, RLS on all, `anon` has no privileges, 4 services and 7 business-hour rows.
2. Create the demo users (after the migrations, see the Supabase checklist). Done: one customer,
   one admin (`profiles.role` set by hand in the SQL editor).
3. **SnapDeploy**: create the container from the GitHub repo (`main`, the `Dockerfile`), port 8080,
   the 10 environment variables above, Auto Deploy on Push **off**. The first build log confirmed
   that the `RUN --mount=type=cache` line in the Dockerfile works there.
4. Verify `GET /health`, `/docs`, and a real token (see Live verification).

## Redeploying, and the SnapDeploy environment trap

Auto Deploy on Push stays **off**, so a redeploy is a deliberate three-step routine (used on
2026-10-01 to ship HSTS):

1. In the SnapDeploy dashboard, switch **Auto Deploy on Push** on.
2. Push a commit to `main` (an empty one is enough: `git commit --allow-empty -m "chore: trigger
   SnapDeploy redeploy" && git push`), after CI is green on the code you want live.
3. Switch **Auto Deploy on Push** off again. Do not leave it on: it would deploy commits that
   CI has not passed.

**SnapDeploy auto-detects environment variables from the repository and can override the values
you set in the dashboard.** On the 2026-10-01 redeploy it replaced `APP_ENV` with `development`
(which silently turns HSTS off). The likely source is `.env.example`, which holds development
values (`APP_ENV=development`, `CALENDAR_ENABLED=false`, a local `SUPABASE_URL` and
`DATABASE_URL`), but that has not been confirmed. **After every deploy, open the environment
variables and check all ten**, above all:

- `APP_ENV` = `production` (otherwise no HSTS)
- `CALENDAR_ENABLED` = `true` (otherwise bookings are not synced to Google Calendar; the app only
  logs a warning)
- `SUPABASE_URL` is the hosted project URL, not `http://127.0.0.1:54321`
- `DATABASE_URL` is the session pooler string, not the local one (a wrong value does not stop
  the app starting: database requests answer 503)
- `DB_POOL_MAX_SIZE` = `5`

Then check `GET /health` and the `Strict-Transport-Security` header
(`curl -sI -A curl/8 <url>/health` returns 405 for HEAD, use `curl -s -D - -o /dev/null <url>/health`).

## Live verification (2026-10-01)

- `GET /health` answers `{"status":"ok"}` (the first call after sleep took about 29 s) and
  `/docs` and `/openapi.json` answer 200.
- `GET /` returns the standard `not_found` error body; `GET /bookings` without a token returns
  the standard 401 body.
- Responses carry the security headers; HSTS (below) is added by this change and is visible
  after the next manual deploy.
- **Real tokens, hosted JWKS** (password grant for the two hand-made users, nothing printed but
  codes and counts): both sign-ins return 200 with an ES256 token (`aud=authenticated`,
  `is_anonymous=false`, one hour lifetime). The app's own verifier accepts both against the hosted
  JWKS, and the live API accepts them too.
- `GET /bookings`: 200 with 0 items for the customer and for the admin (this also proves the
  container reaches the database through the session pooler).
- `GET /admin/bookings`: **403 `forbidden` for the customer, 200 for the admin**. Admin rights
  come only from `profiles.role`; the token's `role` claim is `authenticated` for both.
- No bookings were created in production.

**Cloudflare quirk.** SnapDeploy sits behind Cloudflare, which answers `403` with
`error code: 1010` (a plain-text body, not our error format) to requests with Python's default
`Python-urllib` User-Agent. The same request with any normal `User-Agent` reaches the app. Any
script that calls the live API (the Phase 9 token script, demo clients) must set a `User-Agent`;
`curl`, browsers and Swagger are unaffected. A 403 with that plain body is the edge, not the app.

## HSTS

The app sends `Strict-Transport-Security: max-age=300` on every response when `APP_ENV=production`
(`app/core/hardening.py`). HTTPS is confirmed on the live URL, which is served through the host's
proxy; the header is ignored by browsers over plain HTTP. It is deliberately short, with no
`includeSubDomains` and no `preload`, because browsers cache it and a mistake must expire quickly.
Raise `HSTS_VALUE` in steps (a day, a week, a month) once nothing breaks.

## Production migrations workflow

`.github/workflows/migrate-production.yml` is manual only. It links the hosted project and runs
`supabase db push` (dry run by default). It needs the repository secrets
`SUPABASE_ACCESS_TOKEN`, `SUPABASE_DB_PASSWORD` and `SUPABASE_PROJECT_ID`. The access token is a
project-scoped token (Read-only preset, Migrations: Write) that **expires after 90 days**
(created 2026-10-01, so around 2026-12-30): create a new one and replace the secret before then.
The workflow writes an empty `supabase/signing_keys.json` first, because every CLI command loads
`config.toml`, which names that file; the placeholder is never used against production.

## Hosted Supabase checklist

1. New project, ref `dhovoboznckrknphwpir`, region Southeast Asia (Singapore, `ap-southeast-1`).
   Keep the database password in a password manager. Created with the **Data API disabled**,
   **"Automatically expose new tables" off** and **automatic RLS off**: the app never uses the
   Data API (PostgREST). It reaches Postgres directly through the session pooler and uses
   Supabase only for Auth, so the JWKS endpoint (`/auth/v1/...`, GoTrue) is unaffected. Our
   migrations enable RLS per table and pgTAP verifies it, so automatic RLS adds nothing.
   **Verified (2026-10-01):** with the Data API disabled, `GET /auth/v1/.well-known/jwks.json`
   still answers and lists one ES256 (P-256) key, after migrating the legacy HS256 secret and
   rotating. A real user token was later verified against it (see Live verification).
2. *Settings, JWT Signing Keys*: **Migrate JWT secret**, then **Rotate keys** so an ES256 or RS256
   key is active. The API verifies tokens only against
   `https://<ref>.supabase.co/auth/v1/.well-known/jwks.json`; with only the legacy secret it
   would answer 503.
3. *Authentication, Sign In / Providers* (checked 2026-10-01): anonymous sign-ins **off**,
   "Allow new users to sign up" **off** (sign-ups are closed for the portfolio demo), "Confirm
   email" **on**, manual identity linking **off**, the Email provider **enabled** (hand-made demo users
   sign in with a password), Auth rate limits at their defaults. The API rejects anonymous tokens anyway.
4. Apply the schema with the manual **Production migrations** workflow (`supabase db push`).
5. **Only after step 4**, create the 1 to 2 demo users by hand (*Authentication, Users, Add user*,
   with "Auto Confirm User" ticked, since sign-up and confirmation emails are not in play).
   Order matters: the `on_auth_user_created` trigger creates each user's `profiles` row, and it
   exists only after the migrations. A user created earlier has no profile row.

## Demo token flow

Sign-ups are closed, so nobody can register against production. For the demo, a small script
(Phase 9) signs a demo user in with Supabase's password grant
(`POST {SUPABASE_URL}/auth/v1/token?grant_type=password` with the project's publishable key) and
prints **only the access token**, to paste into Swagger's *Authorize* button. The demo email and
password and the publishable key are read from a local env file outside the repo (for example
`~/.secrets/booking-agent.env`); the script never prints, logs or stores the password or the
refresh token. Access tokens are short lived (one hour by default). The API itself still has no
login endpoint: it only verifies tokens.

## GitHub secrets and variables

| Name | Used by |
|---|---|
| `SUPABASE_ACCESS_TOKEN`, `SUPABASE_DB_PASSWORD`, `SUPABASE_PROJECT_ID` | manual migrations workflow |
| `GOOGLE_CALENDAR_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `GEMINI_API_KEY` | manual live tests workflow (`live.yml`) |

## Live tests (manual)

The real Google Calendar and Gemini tests are **not** part of CI. They live in
`.github/workflows/live.yml` and run only on demand, so they never block a deploy, spend Gemini
quota on every push, or touch the real calendar by accident.

1. Add the repository secrets `GOOGLE_CALENDAR_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON` and
   `GEMINI_API_KEY` (use a **test** calendar; see [google-calendar.md](google-calendar.md)).
2. The workflow appears in the Actions tab once `live.yml` is on `main` (GitHub lists manual
   workflows only from the default branch). Then: GitHub, **Actions** tab, **Live tests (Google Calendar and Gemini)** in the left list,
   **Run workflow**, pick the branch, **Run workflow**.
3. The run sets `REQUIRE_DB=1`, so a missing secret fails the run instead of skipping tests.
   Everything the Google tests create sits in March to April 2031 and is deleted afterwards.

Locally the same tests run with `uv run pytest -m "live_google or live_gemini"` (see
[google-calendar.md](google-calendar.md)).

## Rollback

Deploys are manual, so a rollback is deploying the previous good commit from the SnapDeploy
dashboard (check there for a one-click rollback; not verified). Migrations are forward-only; ship a
new migration to undo schema changes.
