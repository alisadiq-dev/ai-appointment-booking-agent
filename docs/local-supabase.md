# Local Supabase

The API verifies Supabase Auth tokens against the project's **JWKS endpoint** (asymmetric
signing keys, ES256). Locally, that means giving the Supabase CLI a signing key.

## One-time setup per clone

`supabase/config.toml` sets `signing_keys_path = "./signing_keys.json"`. That file holds a
**private key**, so it is gitignored and must be generated on each machine:

```bash
echo '[]' > supabase/signing_keys.json
supabase gen signing-key --algorithm ES256 --append
chmod 600 supabase/signing_keys.json
```

Why the extra `echo`: with the path configured, `supabase start` fails if the file is missing
(`failed to read signing keys: ENOENT`), and `supabase gen signing-key` also refuses to run
without it. Seeding an empty array and using `--append` works non-interactively. Never commit
this file.

## Run

```bash
supabase start          # applies migrations + seed, serves GoTrue with your signing key
supabase test db        # pgTAP: overlap constraint, RLS, privileges
curl http://127.0.0.1:54321/auth/v1/.well-known/jwks.json   # public key only
```

Point the API at it. `.env.example` has every value commented out (the production host would
otherwise apply them); the `sed` one-liner in its header writes a local `.env` with
`SUPABASE_URL=http://127.0.0.1:54321` and the other local defaults.

## Verify a real token (opt-in tests)

```bash
eval "$(supabase status -o env | grep -E '^(API_URL|PUBLISHABLE_KEY)=' | sed 's/^/export /')"
SUPABASE_URL="$API_URL" SUPABASE_PUBLISHABLE_KEY="$PUBLISHABLE_KEY" \
  uv run pytest -m local_supabase
```

These sign up a throwaway user in local GoTrue and check its real token against the real
JWKS endpoint. They are skipped when the variables are not set.

## Database-backed tests

The repository, service and API tests that need real Postgres are skipped unless
`DATABASE_URL` is set (they are marked `local_supabase`). With the stack running:

```bash
export DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
uv run pytest                 # everything; DB tests roll back after themselves
uv run pytest -m local_supabase
```

They cover per-user isolation in SQL, the overlap rule enforced by the exclusion constraint,
and a multi-threaded race for one slot (see [concurrency.md](concurrency.md)). Without
`DATABASE_URL` the rest of the suite still runs.

### `REQUIRE_DB=1`: fail instead of skip

By default, missing configuration *skips* the DB-backed tests (tests marked `requires_db`) and
the real-GoTrue tests (marked `requires_supabase_auth`, which need `SUPABASE_URL` and
`SUPABASE_PUBLISHABLE_KEY`). With `REQUIRE_DB=1` (also `true` or `yes`) a missing variable makes
them fail with a message naming it, so a misconfigured pipeline cannot go green by silently
skipping. CI sets this. Pytest reports the failure as a setup *error* and exits non-zero.

```bash
REQUIRE_DB=1 uv run pytest    # without DATABASE_URL etc.: 37 errors, exit code 1
```

## Run the API against the local stack

```bash
export SUPABASE_URL=http://127.0.0.1:54321
export DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
uv run uvicorn app.main:app --reload     # http://127.0.0.1:8000/docs
```

## Notes

- **Rotation:** keys are cached for `JWKS_CACHE_SECONDS` (default 300). A token signed by a
  key not in the cache triggers one JWKS refetch per 30 s (PyJWT cooldown), so a freshly
  rotated key can take up to about 30 s to be accepted.
- **Outages:** if the JWKS endpoint is unreachable, times out (`JWKS_TIMEOUT_SECONDS`,
  default 5) or returns invalid data, requests get `503 auth_unavailable` and an ERROR log
  line names the URL. Tokens are never accepted without a verified signature.
- **Hosted projects:** enable asymmetric JWT signing keys in the Supabase dashboard and set
  `SUPABASE_URL` to the project URL. Not yet verified against a hosted project (Phase 8).
