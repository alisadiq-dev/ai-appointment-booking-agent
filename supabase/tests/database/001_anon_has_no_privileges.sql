begin;
select plan(2);

-- Every table, view, materialized view, partitioned and foreign table in public.
-- Fails and lists the offenders if anon holds ANY table-level or column-level privilege.
select is(
  array(
    select c.relname::text
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relkind in ('r', 'p', 'v', 'm', 'f')
      and (
        has_table_privilege('anon', c.oid,
          'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
        or has_any_column_privilege('anon', c.oid, 'SELECT,INSERT,UPDATE,REFERENCES')
      )
    order by c.relname
  ),
  array[]::text[],
  'anon has no privileges on any public table'
);

-- Guard against passing vacuously on an empty schema.
select cmp_ok(
  (select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'public' and c.relkind in ('r', 'p')),
  '>=', 5::bigint,
  'public schema contains the expected tables'
);

select * from finish();
rollback;
