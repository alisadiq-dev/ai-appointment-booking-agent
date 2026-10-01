# Google Calendar

The business calendar is accessed as a **service account** that the calendar is shared with.
There is no OAuth user flow and no domain-wide delegation.

## How the integration behaves

- **Interface:** `CalendarPort` (`app/services/ports.py`); `app/integrations/google_calendar.py`
  is the Google implementation. Tests mock the HTTP layer, so no credentials are needed.
- **Scope:** `https://www.googleapis.com/auth/calendar.events` only (create, patch, delete and
  list events; no calendar settings or sharing).
- **Event ids:** the booking UUID in hex. Google accepts only `a-v` and `0-9` in custom ids
  (5 to 1024 characters), which hex satisfies. A retried create cannot make a duplicate:
  a `409` is treated as "already created".
- **No attendees:** Google requires domain-wide delegation for a service account to invite
  attendees, so customer details go in the event title and description instead.
- **Retries:** each call has a timeout (`CALENDAR_TIMEOUT_SECONDS`) and retries `5xx`, `429`
  and `403 rateLimitExceeded` with exponential backoff (`CALENDAR_NUM_RETRIES`).
- **Fail closed:** when retries run out, the adapter raises `CalendarUnavailableError`
  (`503 calendar_unavailable`) and logs the status, never credentials or identifiers.
- **Deleting** an event that is already gone (`404`/`410`) is success. **Updating** one that is
  gone raises `CalendarEventNotFoundError`.
- **Busy time:** `list_busy` returns events added by hand (manual blocks, holidays). It skips
  events this app created (marked `source=ai-appointment-agent`, already covered by the
  bookings table), cancelled events and events marked *free*. An all-day event blocks the whole
  local day in `BUSINESS_TIMEZONE`.
- `httplib2` is not thread-safe, so each thread builds its own client.

## One-time setup

1. In Google Cloud, create or pick a project and **enable the Google Calendar API**.
2. Create a **service account** (no IAM roles needed) and download a **JSON key**. Store it
   outside the repository, for example `~/.secrets/`, mode `600`.
3. In Google Calendar, create a **dedicated calendar** (not the account's primary one).
   *Settings and sharing, Share with specific people*: add the service account's email (the
   `client_email` field in the key file) with permission **Make changes to events**.
4. Copy the **Calendar ID** from *Settings and sharing, Integrate calendar*. It usually ends in
   `@group.calendar.google.com`. The service account's own `primary` calendar is empty, so use
   the explicit id.
5. Put the values in an env file that is **not in the repo**:

   ```bash
   # ~/.secrets/booking-agent.env   (chmod 600)
   export CALENDAR_ENABLED=true
   export GOOGLE_CALENDAR_ID='...@group.calendar.google.com'
   export GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account", ...one line...}'
   ```

   On Cloud Run, set the same variables from Secret Manager. With `CALENDAR_ENABLED=true`, the
   app refuses to start if the id or key is missing or the key is not valid service account JSON.

## Live tests

`tests/integration/test_google_calendar_live.py` talks to the real API. It is skipped unless
`GOOGLE_CALENDAR_ID` and `GOOGLE_SERVICE_ACCOUNT_JSON` are set, and **fails** instead when
`REQUIRE_DB=1` (see [local-supabase.md](local-supabase.md)).

```bash
source ~/.secrets/booking-agent.env && uv run pytest -m live_google -v -s --no-cov
```

Safety: everything it creates lives in March to April 2031 and has a summary starting with
`LIVE TEST`. Each test deletes what it made, and a fixture sweeps that window before and after
and asserts nothing is left (it prints `LIVE-CLEANUP: stale_before=… swept_at_end=…
leftover_after=…`). Events outside that window, or without that prefix, are never touched. Use a
dedicated test calendar anyway.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `404 notFound` on the calendar, and the service account sees **0 calendars** | The calendar is not shared with the service account (check the email matches `client_email`), or `GOOGLE_CALENDAR_ID` is for a different calendar. Sharing takes effect immediately; no acceptance step. |
| `403 forbidden` / `insufficientPermissions` | Shared with *See all event details* only. Change it to **Make changes to events**. |
| `403 accessNotConfigured` | The Calendar API is not enabled in the key's project. |
| `401` / `invalid_grant` | Key deleted or disabled, or the machine clock is far off. |
| `403 rateLimitExceeded` / `429` | Retried automatically with backoff; if it persists you will see `503 calendar_unavailable`. |

The log lines name the failing operation and HTTP status, and point at sharing for `403`/`404`,
but never print the calendar id, the service account email or any key material.

## Secrets rules

Never commit, paste or log the key file, `GOOGLE_SERVICE_ACCOUNT_JSON` or the calendar id.
Settings hold them as `SecretStr` and set `hide_input_in_errors`, so validation errors and
reprs do not echo them (tests assert this). `.gitignore` blocks `.secrets/`, `*.pem` and
`service-account*.json`.
