-- Historique des conversations du chatbot EMSI.
-- Chaque utilisateur ne voit que SES conversations (Row Level Security).

-- 1. Une conversation = un fil de discussion (liste "Historique").
create table public.conversations (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  title       text not null,                     -- la 1re question
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now() -- pour trier : la plus récente en haut
);

-- 2. Les messages d'une conversation.
create table public.chat_messages (
  id               bigint generated always as identity primary key,
  conversation_id  uuid not null references public.conversations (id) on delete cascade,
  user_id          uuid not null default auth.uid() references auth.users (id) on delete cascade,
  role             text not null check (role in ('user', 'assistant')),
  content          text not null,
  photo_path       text,   -- photo envoyée : chemin dans le bucket "chat-photos"
  details          jsonb,  -- réponse du backend : pages, images, agents, outils, durée
  created_at       timestamptz not null default now()
);

create index conversations_user_updated_idx on public.conversations (user_id, updated_at desc);
create index chat_messages_conversation_idx on public.chat_messages (conversation_id, id);
create index chat_messages_user_idx on public.chat_messages (user_id);

-- 3. Sécurité : chacun ne lit / écrit que ses propres lignes.
alter table public.conversations enable row level security;
alter table public.chat_messages enable row level security;

create policy "own conversations" on public.conversations
  for all to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

create policy "own messages" on public.chat_messages
  for all to authenticated
  using ((select auth.uid()) = user_id)
  with check (
    (select auth.uid()) = user_id
    -- on ne peut écrire que dans SA conversation
    and exists (select 1 from public.conversations c
                where c.id = conversation_id and c.user_id = (select auth.uid()))
  );

-- 4. Photos envoyées : bucket PRIVÉ, un dossier par utilisateur (<user_id>/...).
insert into storage.buckets (id, name, public) values ('chat-photos', 'chat-photos', false);

create policy "own chat photos: read" on storage.objects
  for select to authenticated
  using (bucket_id = 'chat-photos' and (storage.foldername(name))[1] = (select auth.uid())::text);

create policy "own chat photos: upload" on storage.objects
  for insert to authenticated
  with check (bucket_id = 'chat-photos' and (storage.foldername(name))[1] = (select auth.uid())::text);

create policy "own chat photos: delete" on storage.objects
  for delete to authenticated
  using (bucket_id = 'chat-photos' and (storage.foldername(name))[1] = (select auth.uid())::text);
