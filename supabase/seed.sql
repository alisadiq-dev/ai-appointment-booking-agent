insert into public.services (name, duration_minutes, price) values
  ('Haircut',         30, 25.00),
  ('Kids Haircut',    20, 15.00),
  ('Beard Trim',      20, 15.00),
  ('Haircut & Beard', 45, 35.00)
on conflict (name) do nothing;

-- ISO weekday: 1 = Monday ... 7 = Sunday. NULL = closed.
insert into public.business_hours (day_of_week, open_time, close_time) values
  (1, '09:00', '18:00'),
  (2, '09:00', '18:00'),
  (3, '09:00', '18:00'),
  (4, '09:00', '18:00'),
  (5, '09:00', '18:00'),
  (6, '10:00', '16:00'),
  (7, null,    null)
on conflict (day_of_week) do nothing;
