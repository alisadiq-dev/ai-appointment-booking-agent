begin;
select plan(4);

-- A table created AFTER the default-privileges migration must start with no grants.
create table public.zz_default_privileges_probe (id int);

select ok(
  not has_table_privilege('anon', 'public.zz_default_privileges_probe',
    'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'),
  'new table: anon has no privileges');

select ok(
  not has_table_privilege('authenticated', 'public.zz_default_privileges_probe',
    'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'),
  'new table: authenticated has no privileges');

-- Same check for every existing table: authenticated only ever gets SELECT, never writes.
select is(
  array(
    select c.relname::text
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relkind in ('r', 'p')
      and has_table_privilege('authenticated', c.oid,
        'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
    order by c.relname
  ),
  array[]::text[],
  'authenticated has no write privileges on any public table');

select ok(
  not has_table_privilege('authenticated', 'public.conversation_sessions', 'SELECT'),
  'authenticated cannot read conversation_sessions');

select * from finish();
rollback;
