-- Sequences: new ones in public start without anon/authenticated grants,
-- and existing ones (e.g. business_hours_id_seq) lose theirs.
alter default privileges for role postgres in schema public
  revoke all on sequences from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;

-- Trigger function: nobody needs EXECUTE on it (trigger permission is checked at creation).
revoke execute on function public.set_updated_at() from authenticated;
