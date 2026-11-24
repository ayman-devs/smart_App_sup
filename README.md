# smart_app_pi_lafnoune

A new Flutter project.

## Getting Started

This project is a starting point for a Flutter application.

A few resources to get you started if this is your first Flutter project:

- [Learn Flutter](https://docs.flutter.dev/get-started/learn-flutter)
- [Write your first Flutter app](https://docs.flutter.dev/get-started/codelab)
- [Flutter learning resources](https://docs.flutter.dev/reference/learning-resources)

For help getting started with Flutter development, view the
[online documentation](https://docs.flutter.dev/), which offers tutorials,
samples, guidance on mobile development, and a full API reference.

## Avatar de profil

L’application enregistre le chemin de l’avatar dans les métadonnées de
l’utilisateur Supabase (`avatar_path`) et l’image dans le bucket privé
`avatars`. Aucun changement de table `profiles` n’est nécessaire. Dans
Supabase, ouvrez **SQL Editor**, puis exécutez :

```sql
insert into storage.buckets (
  id, name, public, file_size_limit, allowed_mime_types
)
values (
  'avatars',
  'avatars',
  false,
  2097152,
  array['image/jpeg', 'image/png', 'image/webp']
)
on conflict (id) do update set
  public = excluded.public,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "Users can view their own avatars" on storage.objects;
drop policy if exists "Users can upload their own avatars" on storage.objects;
drop policy if exists "Users can update their own avatars" on storage.objects;
drop policy if exists "Users can delete their own avatars" on storage.objects;

create policy "Users can view their own avatars"
on storage.objects for select
to authenticated
using (
  bucket_id = 'avatars'
  and (storage.foldername(name))[1] = (select auth.uid()::text)
);

create policy "Users can upload their own avatars"
on storage.objects for insert
to authenticated
with check (
  bucket_id = 'avatars'
  and (storage.foldername(name))[1] = (select auth.uid()::text)
);

create policy "Users can update their own avatars"
on storage.objects for update
to authenticated
using (
  bucket_id = 'avatars'
  and (storage.foldername(name))[1] = (select auth.uid()::text)
)
with check (
  bucket_id = 'avatars'
  and (storage.foldername(name))[1] = (select auth.uid()::text)
);

create policy "Users can delete their own avatars"
on storage.objects for delete
to authenticated
using (
  bucket_id = 'avatars'
  and (storage.foldername(name))[1] = (select auth.uid()::text)
);
```

Le bucket est privé : l’application demande une URL signée pour afficher une
image. Les politiques limitent lecture, ajout, remplacement et suppression au
dossier portant l’identifiant de l’utilisateur connecté. La clé `service_role`
ne doit jamais être utilisée dans l’application.

Pour tester dans Chrome :

1. Appliquer le SQL ci-dessus dans le projet Supabase configuré dans
   `lib/supabase_config.dart`.
2. Lancer `flutter run -d chrome`, puis se connecter.
3. Ouvrir le Drawer, choisir **Mon profil**, puis **Changer la photo**.
4. Choisir une image JPG, PNG ou WebP de moins de 2 Mo et vérifier l’aperçu,
   le message de succès, puis l’avatar dans le Drawer.
5. Recharger l’application et vérifier que l’avatar est toujours affiché.
