create table public.services (
  id               uuid primary key default gen_random_uuid(),
  name             text not null unique,
  duration_minutes integer not null check (duration_minutes > 0),
  price            numeric(10, 2) not null check (price >= 0),
  created_at       timestamptz not null default now()
);

-- Wall-clock times in the business timezone (BUSINESS_TIMEZONE env var, applied in app code).
-- ISO weekday: 1 = Monday ... 7 = Sunday. NULL open/close means closed that day.
create table public.business_hours (
  id          smallint generated always as identity primary key,
  day_of_week smallint not null unique check (day_of_week between 1 and 7),
  open_time   time,
  close_time  time,
  constraint business_hours_valid check (
    (open_time is null and close_time is null)
    or (open_time is not null and close_time > open_time)
  )
);
