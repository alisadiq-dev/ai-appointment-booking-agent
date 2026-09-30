# Concurrency

How the API behaves when several customers try to book the same slot at the same instant.

## The rule

The database decides. `bookings_no_overlap` is an exclusion constraint on
`tstzrange(start_at, end_at, '[)')` for confirmed bookings, so two overlapping confirmed
bookings can never both be committed, whatever the application does. The service layer only
validates what the database cannot (hours, grid, past) and maps the outcome to `409
slot_unavailable`.

## Finding: losers could get a deadlock instead of a clean conflict

A test with 6 threads, each on its own connection, booking the same slot simultaneously
showed that Postgres does not always raise `exclusion_violation` (SQLSTATE `23P01`) for the
loser. Sometimes both transactions insert their row and then each waits for the other's
uncommitted conflicting row during the exclusion check. That is a deadlock, and Postgres
aborts one transaction with `deadlock_detected` (`40P01`) after `deadlock_timeout` (1 s by
default).

- **Safety was never at risk.** Nobody was double-booked; the constraint held.
- **The impact was on the loser:** an unhandled `40P01` would have been a 500, after a
  roughly one-second stall.
- **Evidence** (Postgres `pg_stat_database.deadlocks`, 5 runs of the race test, before the
  fix): 77 deadlocks, with runs taking 10 to 31 seconds instead of under a second.

## Fix

Both live in `BookingRepository` (`app/repositories/bookings.py`).

1. **Serialize booking writes with an advisory lock.** Every create and reschedule runs
   `select pg_advisory_xact_lock(7243001)` first, inside its transaction. Competing writers
   queue on the lock instead of racing; the loser waits for the winner to commit and then
   fails immediately and cleanly with `23P01`. The lock is released automatically when the
   request's transaction ends. Cancel does not take it (cancelling never creates a conflict).
2. **Retry a deadlock victim once.** If Postgres still aborts a write with `40P01` for any
   reason, the repository retries once in a fresh transaction block. The retry sees the
   winner's committed row and fails with `23P01`, or succeeds if the winner rolled back. A
   second deadlock is reported as slot taken. The loser never sees a 500.

The exclusion constraint remains the rule; the lock only orders access to it. Trade-off:
booking writes are serialized across the whole database. That is fine for one business
calendar with low write volume. If this ever becomes multi-tenant, use a per-business lock
key instead of the single constant.

### Commit before the response

Related: FastAPI cleans up `yield` dependencies *after* the response is sent by default, which
would let a client see `201 Created` before the booking is committed. The per-request
connection dependency uses `Depends(..., scope="function")`, so the transaction commits (or
rolls back) before the response goes out. `tests/integration/test_database_wiring.py` asserts
the order: handler, commit, response.

## Stress test results (after the fix)

`tests/integration/test_booking_service_db.py::test_concurrent_requests_for_one_slot_have_exactly_one_winner`
runs 8 rounds; each round has 6 threads on separate connections booking the same slot at once,
on committed data. Every round must end with exactly one booking and five clean conflicts, and
no thread may raise anything else.

| | Before the lock | After the lock |
|---|---|---|
| Deadlocks reported by Postgres | 77 over 5 runs | **0** over 10 runs (80 rounds, 480 attempts) |
| Wall time per run of this test | 10 to 31 s | **about 0.35 s** |
| Failures | none (deadlocks were retried) | **none** |

The retry path is also covered without a database in `tests/unit/test_booking_repository_retry.py`,
using a scripted connection that raises `DeadlockDetected` on demand (retry once, then slot
taken, lock taken before every attempt).

## Reproduce

```bash
export DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres   # supabase start
psql "$DATABASE_URL" -tA -c "select deadlocks from pg_stat_database where datname='postgres'"
for i in $(seq 1 10); do uv run pytest tests/integration/test_booking_service_db.py -k concurrent -q --no-cov; done
psql "$DATABASE_URL" -tA -c "select deadlocks from pg_stat_database where datname='postgres'"
```

The two counter readings should be equal. To see the original problem, remove the
`pg_advisory_xact_lock` call from `_write_returning_row` and rerun (the retry still keeps the
test green, but the counter rises and runs slow down).
