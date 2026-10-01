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

Set **`DB_POOL_MAX_SIZE=5`** in production (the code default is 10). A session pooler gives each
client connection its own backend connection, and the free plan allows only a small number of
them; a pool of 10 plus a restart that overlaps the old process during a deploy could exhaust the
limit and lock the service out. Five is enough because one chat request holds one connection
for a whole turn and the per-user turn lock and rate limit bound the concurrency.

## Environment variables (Render dashboard only, never in git)

| Variable | Value |
|---|---|
| `APP_ENV` | `production` |
| `SUPABASE_URL` | `https://<project-ref>.supabase.co` |
| `DATABASE_URL` | session pooler string (secret) |
| `DB_POOL_MAX_SIZE` | `5` |
| `BUSINESS_TIMEZONE` | `Asia/Karachi` (or your zone) |
| `CALENDAR_ENABLED` | **`true`** (the app refuses to start without the two values below) |
| `GOOGLE_CALENDAR_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON` | see [google-calendar.md](google-calendar.md) (secret) |
| `GEMINI_API_KEY` | Google AI Studio key (secret; without it the agent only sends the fallback reply) |

`render.yaml` lists the secrets with `sync: false`, so Render asks for their values in the
dashboard on first creation and they never enter the repository.

## Hosted Supabase checklist

1. New project in Southeast Asia (Singapore); keep the database password in a password manager.
2. *Settings, JWT Signing Keys*: **Migrate JWT secret**, then **Rotate keys** so an ES256 or RS256
   key is active. The API verifies tokens only against
   `https://<ref>.supabase.co/auth/v1/.well-known/jwks.json`; with only the legacy secret it
   would answer 503.
3. Leave anonymous sign-ins disabled (the API rejects them anyway).
4. Apply the schema with the manual **Production migrations** workflow (`supabase db push`).

## GitHub secrets and variables

| Name | Used by |
|---|---|
| `SUPABASE_ACCESS_TOKEN`, `SUPABASE_DB_PASSWORD`, `SUPABASE_PROJECT_ID` | manual migrations workflow |
| `GOOGLE_CALENDAR_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `GEMINI_API_KEY` | optional live tests in CI (skipped when absent) |

## Rollback

Render keeps previous deploys: *Events, Rollback* in the dashboard redeploys the last good image.
Migrations are forward-only; ship a new migration to undo schema changes.
