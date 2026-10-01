# Deployment (Render free plan + hosted Supabase)

The API runs as a Docker web service on **Render's free plan** (region Singapore), built from the
repo's `Dockerfile`. The database and auth are a **hosted Supabase free project** (Singapore).
Render deploys automatically, but only after the GitHub checks on `main` pass
(`autoDeployTrigger: checksPass` in `render.yaml`).

## What the free plan means for this app

| Limit (Render free) | Effect here |
|---|---|
| **One instance only** | Satisfies the in-memory chat rate limiter ([chat-api.md](chat-api.md)): one process, one set of counters. `numInstances` stays 1. Do not move to a paid multi-instance plan without a shared limiter (for example Redis). |
| **Spins down after 15 minutes without inbound traffic** | The next request is a **cold start**: about one minute before the service answers. Clients should expect a slow first call after idle. We deliberately do not run a keep-alive ping (it would burn nearly all of the monthly hours). |
| Restart or spin-down | All in-memory state is lost: the rate-limit counters reset. Bookings and chat sessions are safe, they live in Postgres. |
| 750 free instance hours per month | Enough for one always-reachable service; exceeding it suspends the service until the next month. |
| Ephemeral filesystem, no SSH or shell | The app writes nothing to disk. Logs go to stdout as JSON (see Render's log view). |

## Database connection: session pooler, pool size 5

Render cannot reach Supabase's direct host (`db.<ref>.supabase.co`, IPv6 only). Use the **Session
pooler** string from the dashboard's *Connect* dialog: host `aws-<n>-<region>.pooler.supabase.com`,
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

Set in [render.yaml](../render.yaml) (not secret, committed):

| Variable | Value |
|---|---|
| `APP_ENV` | `production` |
| `LOG_LEVEL` | `INFO` |
| `SUPABASE_URL` | `https://dhovoboznckrknphwpir.supabase.co` |
| `BUSINESS_TIMEZONE` | `Asia/Karachi` |
| `DB_POOL_MAX_SIZE` | `5` |
| `CALENDAR_ENABLED` | **`true`** (the app refuses to start without the two Google values) |

Entered **by hand in the Render dashboard** (`sync: false` in the blueprint, so they never enter
the repository):

| Variable | What it is |
|---|---|
| `DATABASE_URL` | Session pooler string with `?sslmode=require` (see above) |
| `GOOGLE_CALENDAR_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON` | see [google-calendar.md](google-calendar.md) |
| `GEMINI_API_KEY` | Google AI Studio key (without it the agent only sends the fallback reply) |

`PORT` is set by Render; the Dockerfile reads it.

## First deploy, in order

1. Merge the PR that contains `render.yaml` and the workflows (a blueprint is read from the
   branch you pick, and manual workflows are listed only from `main`).
2. **Migrations**: Actions tab, **Production migrations**, Run workflow with `dry_run` ticked,
   check the list (7 migrations), run again with `dry_run` **unticked** and `include_seed`
   **ticked** (the seed is repeat safe). A permission error here means the access token scope
   needs a look; do not widen it blindly.
3. Create the demo users in the Supabase dashboard (after the migrations, see below).
4. **Render**: *New, Blueprint*, connect the repo, branch `main`, enter the four secrets, Apply.
5. Verify `https://<service>.onrender.com/health` and `/docs`, then a real chat turn with a demo
   token. Confirm HTTPS, then add HSTS (tasks.md).

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
   rotating. Verifying a real user token against it is still open (tasks.md, Phase 8).
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

Render keeps previous deploys: *Events, Rollback* in the dashboard redeploys the last good image.
Migrations are forward-only; ship a new migration to undo schema changes.
