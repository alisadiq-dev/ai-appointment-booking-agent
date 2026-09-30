begin;
select plan(7);

-- Existing sequences in public: anon/authenticated hold nothing.
select is(
  array(
    select c.relname::text
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relkind = 'S'
      and (has_sequence_privilege('anon', c.oid, 'USAGE,SELECT,UPDATE')
        or has_sequence_privilege('authenticated', c.oid, 'USAGE,SELECT,UPDATE'))
    order by c.relname
  ),
  array[]::text[],
  'anon and authenticated have no privileges on any public sequence');

-- A sequence created AFTER the default-privileges migration starts with no grants.
create table public.zz_sequence_probe (id int generated always as identity);

select ok(
  not has_sequence_privilege('anon',
    pg_get_serial_sequence('public.zz_sequence_probe', 'id'), 'USAGE,SELECT,UPDATE'),
  'new sequence: anon has no privileges');
select ok(
  not has_sequence_privilege('authenticated',
    pg_get_serial_sequence('public.zz_sequence_probe', 'id'), 'USAGE,SELECT,UPDATE'),
  'new sequence: authenticated has no privileges');

-- No function in public is executable by anon or authenticated (backend-only design).
select is(
  array(
    select p.proname::text
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and (has_function_privilege('anon', p.oid, 'EXECUTE')
        or has_function_privilege('authenticated', p.oid, 'EXECUTE'))
    order by p.proname
  ),
  array[]::text[],
  'anon and authenticated cannot execute any function in public');

-- is_admin() is the one deliberate exception, and only for authenticated.
select ok(has_function_privilege('authenticated', 'private.is_admin()', 'EXECUTE'),
  'authenticated can execute private.is_admin()');
select ok(not has_function_privilege('anon', 'private.is_admin()', 'EXECUTE'),
  'anon cannot execute private.is_admin()');

-- Guard against passing vacuously.
select cmp_ok(
  (select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'public' and c.relkind = 'S'),
  '>=', 1::bigint, 'public schema contains at least one sequence');

select * from finish();
rollback;
