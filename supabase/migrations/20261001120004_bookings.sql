create table public.bookings (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references public.profiles (id) on delete cascade,
  service_id      uuid not null references public.services (id) on delete restrict,
  start_at        timestamptz not null,   -- always stored in UTC
  end_at          timestamptz not null,
  status          text not null default 'confirmed'
                  check (status in ('confirmed', 'cancelled')),
  google_event_id text,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),

  constraint bookings_end_after_start check (end_at > start_at),

  -- No two confirmed bookings may overlap. '[)' allows back-to-back slots.
  -- Cancelled rows fall outside the WHERE predicate, so they free their slot.
  -- Violation raises SQLSTATE 23P01 (exclusion_violation).
  constraint bookings_no_overlap
    exclude using gist (tstzrange(start_at, end_at, '[)') with &&)
    where (status = 'confirmed')
);

-- "My bookings" list, newest first
create index bookings_user_start_idx
  on public.bookings (user_id, start_at desc);

-- One Google Calendar event maps to at most one booking
create unique index bookings_google_event_uidx
  on public.bookings (google_event_id) where google_event_id is not null;

create trigger bookings_updated_at before update on public.bookings
  for each row execute function public.set_updated_at();
