create table public.conversation_sessions (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null unique references public.profiles (id) on delete cascade,
  state      jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create trigger sessions_updated_at before update on public.conversation_sessions
  for each row execute function public.set_updated_at();
