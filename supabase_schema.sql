-- HSK story storage for Supabase.
--
-- Run this once in the Supabase dashboard: SQL Editor -> New query -> paste ->
-- Run.  The FastAPI backend uses the project's secret key (SUPABASE_KEY) and
-- proxies all reads/writes, so RLS stays enabled and no public policy is added:
-- the publishable/anon key cannot read or write the stories table.

create table if not exists public.stories (
    id          text primary key,
    title       text        not null default '无题',
    level       text,
    created     timestamptz,
    seed_noun   text,
    bank        boolean     not null default false,
    finished    boolean     not null default false,
    n_han       integer,
    n_questions integer     not null default 0,
    data        jsonb       not null,
    updated_at  timestamptz not null default now()
);

comment on table public.stories is
    'HSK stories served by the FastAPI reader; data holds the full story JSON.';
comment on column public.stories.data is
    'Complete story payload (text, sentences, tokens, questions, metrics, ...).';

create index if not exists stories_created_idx
    on public.stories (created desc nulls last);
create index if not exists stories_level_idx
    on public.stories (level);

create or replace function public.touch_stories_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists stories_touch_updated_at on public.stories;
create trigger stories_touch_updated_at
    before update on public.stories
    for each row
    execute function public.touch_stories_updated_at();

alter table public.stories enable row level security;

-- No policies on purpose.  The secret key (service_role) bypasses RLS, and the
-- backend is the only component that should ever touch this table.
revoke all on table public.stories from anon, authenticated;
