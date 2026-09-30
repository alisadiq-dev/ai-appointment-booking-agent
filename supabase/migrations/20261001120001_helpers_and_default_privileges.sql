-- Private schema for helper functions that must not be exposed through the Data API.
create schema if not exists private;

-- Supabase grants new tables to anon/authenticated by default. Remove that so every
-- table starts locked down and gets explicit, minimal grants (see the RLS migration).
alter default privileges for role postgres in schema public
  revoke all on tables from anon, authenticated;

create function public.set_updated_at() returns trigger
language plpgsql set search_path = '' as $$
begin
  new.updated_at = now();
  return new;
end $$;
revoke execute on function public.set_updated_at() from public, anon;
