begin;
select plan(16);

insert into auth.users (id, email) values
  ('00000000-0000-0000-0000-0000000000a1', 'u1@example.test'),
  ('00000000-0000-0000-0000-0000000000a2', 'u2@example.test'),
  ('00000000-0000-0000-0000-0000000000a3', 'admin@example.test');
update public.profiles set role = 'admin' where id = '00000000-0000-0000-0000-0000000000a3';

insert into public.services (id, name, duration_minutes, price)
values ('00000000-0000-0000-0000-0000000000b1', 'zz-test-service', 30, 10);

insert into public.bookings (user_id, service_id, start_at, end_at) values
  ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1',
   '2030-01-07 10:00+00', '2030-01-07 10:30+00'),
  ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000b1',
   '2030-01-07 11:00+00', '2030-01-07 11:30+00');

insert into public.conversation_sessions (user_id, state)
values ('00000000-0000-0000-0000-0000000000a1', '{"step": "x"}');

select is((select count(*) from public.conversation_sessions), 1::bigint,
          'setup: a session row exists (visible to the backend role)');

-- ---------- user 1 (authenticated) ----------
set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"00000000-0000-0000-0000-0000000000a1","role":"authenticated"}', true);

select is((select count(*) from public.bookings), 1::bigint, 'user 1 sees only their own booking');
select is((select count(*) from public.bookings
           where user_id = '00000000-0000-0000-0000-0000000000a2'),
          0::bigint, 'user 1 cannot read user 2''s bookings');
select is((select count(*) from public.profiles), 1::bigint, 'user 1 sees only their own profile');
select ok((select count(*) from public.services) >= 1, 'user 1 can read services');
select lives_ok('select * from public.business_hours', 'user 1 can read business hours');
select is((select private.is_admin()), false, 'user 1 is not an admin');

select throws_ok('select * from public.conversation_sessions', '42501', null,
                 'user 1 cannot read conversation_sessions');
select throws_ok($$insert into public.bookings (user_id, service_id, start_at, end_at)
  values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1',
          '2030-02-01 10:00+00', '2030-02-01 10:30+00')$$,
  '42501', null, 'user 1 cannot insert bookings directly');
select throws_ok($$update public.bookings set status = 'cancelled'$$,
  '42501', null, 'user 1 cannot update bookings directly');
select throws_ok($$delete from public.bookings$$,
  '42501', null, 'user 1 cannot delete bookings directly');

-- ---------- admin ----------
select set_config('request.jwt.claims',
  '{"sub":"00000000-0000-0000-0000-0000000000a3","role":"authenticated"}', true);

select is((select private.is_admin()), true, 'admin user is recognised');
select is((select count(*) from public.bookings), 2::bigint, 'admin sees all bookings');

-- ---------- anon ----------
reset role;
set local role anon;
select throws_ok('select * from public.bookings', '42501', null, 'anon cannot read bookings');
select throws_ok('select * from public.services', '42501', null, 'anon cannot read services');
select throws_ok('select private.is_admin()', '42501', null, 'anon cannot call private.is_admin()');

reset role;
select * from finish();
rollback;
