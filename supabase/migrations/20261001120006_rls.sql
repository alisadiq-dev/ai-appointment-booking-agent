alter table public.profiles              enable row level security;
alter table public.services              enable row level security;
alter table public.business_hours        enable row level security;
alter table public.bookings              enable row level security;
alter table public.conversation_sessions enable row level security;

-- Start from zero (explicit, in case default privileges ever change), then grant read-only.
revoke all on public.profiles, public.services, public.business_hours,
              public.bookings, public.conversation_sessions from anon, authenticated;

-- conversation_sessions is deliberately NOT granted: RLS enabled, no policies, no grants.
-- The Data API can never read or write session state; only the backend role can.
grant select on public.profiles, public.services, public.business_hours,
                public.bookings to authenticated;

create policy "read own profile or admin" on public.profiles
  for select to authenticated
  using ((select auth.uid()) = id or (select private.is_admin()));

create policy "read services" on public.services
  for select to authenticated using (true);

create policy "read business hours" on public.business_hours
  for select to authenticated using (true);

create policy "read own bookings or admin" on public.bookings
  for select to authenticated
  using ((select auth.uid()) = user_id or (select private.is_admin()));
