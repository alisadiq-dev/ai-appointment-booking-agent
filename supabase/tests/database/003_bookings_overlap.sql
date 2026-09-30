begin;
select plan(13);

insert into auth.users (id, email) values
  ('00000000-0000-0000-0000-0000000000a1', 'u1@example.test'),
  ('00000000-0000-0000-0000-0000000000a2', 'u2@example.test');

insert into public.services (id, name, duration_minutes, price)
values ('00000000-0000-0000-0000-0000000000b1', 'zz-test-service', 30, 10);

select is((select count(*) from public.profiles
           where id in ('00000000-0000-0000-0000-0000000000a1',
                        '00000000-0000-0000-0000-0000000000a2')),
          2::bigint, 'signup trigger created a profile for each auth user');

-- A: 10:00-10:30 (u1)
select lives_ok($$
  insert into public.bookings (id, user_id, service_id, start_at, end_at, google_event_id)
  values ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000a1',
          '00000000-0000-0000-0000-0000000000b1',
          '2030-01-07 10:00+00', '2030-01-07 10:30+00', 'evt-A')$$,
  'first confirmed booking succeeds');

select throws_ok($$
  insert into public.bookings (user_id, service_id, start_at, end_at)
  values ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000b1',
          '2030-01-07 10:15+00', '2030-01-07 10:45+00')$$,
  '23P01', null, 'overlapping confirmed booking is rejected (even for another user)');

-- C: 10:30-11:00 (u2), back-to-back with A
select lives_ok($$
  insert into public.bookings (id, user_id, service_id, start_at, end_at, google_event_id)
  values ('00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000a2',
          '00000000-0000-0000-0000-0000000000b1',
          '2030-01-07 10:30+00', '2030-01-07 11:00+00', 'evt-C')$$,
  'back-to-back booking is allowed');

-- D: cancelled booking overlapping A
select lives_ok($$
  insert into public.bookings (id, user_id, service_id, start_at, end_at, status)
  values ('00000000-0000-0000-0000-0000000000c3', '00000000-0000-0000-0000-0000000000a2',
          '00000000-0000-0000-0000-0000000000b1',
          '2030-01-07 10:00+00', '2030-01-07 10:30+00', 'cancelled')$$,
  'cancelled booking may overlap a confirmed one');

-- Reschedule C onto a range that overlaps its own old range: must succeed.
select lives_ok($$
  update public.bookings
  set start_at = '2030-01-07 10:40+00', end_at = '2030-01-07 11:10+00'
  where id = '00000000-0000-0000-0000-0000000000c2'$$,
  'rescheduling a booking to a time overlapping itself succeeds');

select is((select google_event_id from public.bookings
           where id = '00000000-0000-0000-0000-0000000000c2'),
          'evt-C', 'reschedule keeps the same row and google_event_id');

-- Reschedule C onto A's time: must fail.
select throws_ok($$
  update public.bookings
  set start_at = '2030-01-07 10:00+00', end_at = '2030-01-07 10:30+00'
  where id = '00000000-0000-0000-0000-0000000000c2'$$,
  '23P01', null, 'rescheduling onto another booking''s time fails');

-- Cancelling A frees its slot.
update public.bookings set status = 'cancelled'
where id = '00000000-0000-0000-0000-0000000000c1';

select lives_ok($$
  insert into public.bookings (id, user_id, service_id, start_at, end_at)
  values ('00000000-0000-0000-0000-0000000000c4', '00000000-0000-0000-0000-0000000000a2',
          '00000000-0000-0000-0000-0000000000b1',
          '2030-01-07 10:00+00', '2030-01-07 10:30+00')$$,
  'cancelling a booking frees its slot');

-- Un-cancelling D while E occupies the slot must fail.
select throws_ok($$
  update public.bookings set status = 'confirmed'
  where id = '00000000-0000-0000-0000-0000000000c3'$$,
  '23P01', null, 're-confirming a cancelled booking into an occupied slot fails');

select throws_ok($$
  insert into public.bookings (user_id, service_id, start_at, end_at)
  values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1',
          '2030-01-08 10:00+00', '2030-01-08 10:00+00')$$,
  '23514', null, 'end_at must be after start_at');

select throws_ok($$
  insert into public.bookings (user_id, service_id, start_at, end_at, google_event_id)
  values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1',
          '2030-01-08 12:00+00', '2030-01-08 12:30+00', 'evt-C')$$,
  '23505', null, 'google_event_id must be unique');

select throws_ok($$
  delete from public.services where id = '00000000-0000-0000-0000-0000000000b1'$$,
  '23503', null, 'a service with bookings cannot be deleted');

select * from finish();
rollback;
