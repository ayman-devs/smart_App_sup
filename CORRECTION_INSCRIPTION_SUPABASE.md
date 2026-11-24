# Correction de l'inscription Supabase

## Symptômes

- Après « Créer mon compte », le message « Vérifiez votre email » s'affiche, mais
  on ne peut pas se connecter ensuite (« Email not confirmed »).
- Le lien reçu par email ouvre `http://localhost:3000` (page introuvable) au lieu
  de l'application.
- Avec un email déjà utilisé, l'app dit « Vérifiez votre email » alors que rien
  n'est envoyé.
- Parfois aucun email n'arrive, ou l'erreur « email rate limit exceeded » s'affiche.
- En APK **release**, l'app ne peut pas joindre Supabase.

## Cause réelle (logs Auth du projet `smart-app-supabase`, 23/11/2026)

| Heure (UTC) | Requête | Erreur |
|-------------|---------|--------|
| 19:21, 19:29 | `POST /signup` (`abdo@gmail.com`) | `400 email_address_invalid` : adresse refusée par Supabase |
| 19:30, 19:31 | `POST /signup` (autre adresse) | `429 over_email_send_rate_limit` : limite d'emails atteinte |

Le seul compte présent dans `auth.users` est celui créé le 03/11. Aucune
inscription n'a abouti depuis.

**Pourquoi :** la confirmation par email est activée et le projet utilise le
**SMTP intégré de Supabase**. Ce service n'envoie que très peu d'emails par heure
et refuse certaines adresses. Si l'email ne part pas, l'inscription est annulée.

**Solution :** dans le dashboard, *Authentication → Sign In / Providers → Email*,
désactiver **Confirm email** pour le développement. Autre option : configurer un
SMTP personnalisé.

Ce réglage **ne peut pas être modifié avec le connecteur Supabase MCP**, qui
n'expose ni la configuration Auth ni le SMTP. Il faut le faire dans le dashboard
ou avec l'API Management et un jeton d'accès personnel.

Les tests se font sur Chrome, sur un port aléatoire (`localhost:63437`). Pour le
web, lancer avec un port fixe qui correspond à la *Site URL* :
`flutter run -d chrome --web-port 3000`.

## Diagnostic (code)

La configuration Auth du projet (`/auth/v1/settings`) indique :

```json
"email": true, "disable_signup": false, "mailer_autoconfirm": false
```

La **confirmation par email est obligatoire**. `signUp()` réussit, mais il ne
renvoie **pas de session**. Le compte reste inactif tant que le lien n'a pas été
cliqué. Le code n'était pas prêt pour ce cas.

| # | Cause | Fichier |
|---|-------|---------|
| 1 | `signUp()` sans `emailRedirectTo` : le lien renvoie vers la *Site URL* (par défaut `localhost:3000`) et la confirmation n'arrive jamais dans l'app. | `lib/screens/register_page.dart` |
| 2 | Aucun deep link déclaré : l'app ne peut pas recevoir le retour du lien. | `AndroidManifest.xml`, `Info.plist` |
| 3 | Email déjà inscrit : Supabase ne renvoie **pas d'erreur** mais un utilisateur sans `identities` (protection anti-énumération). L'app affichait donc un faux succès. | `register_page.dart` |
| 4 | `catch (_)` affichait toujours « Erreur réseau » et masquait la vraie erreur. Les messages Supabase (rate limit, mot de passe faible…) restaient en anglais. | `register_page.dart`, `login_page.dart` |
| 5 | Connexion avant confirmation : erreur « Email not confirmed », sans moyen de renvoyer l'email. | `login_page.dart` |
| 6 | Permission `INTERNET` absente du manifest **main**. Elle n'existe qu'en debug et profile, donc l'APK release ne peut pas joindre Supabase. | `android/app/src/main/AndroidManifest.xml` |

## Corrections appliquées dans le code

- **`lib/supabase_config.dart`** : ajout de `mobileRedirectUrl`
  (`io.supabase.smartapp://login-callback/`) et de `authRedirectUrl`, qui vaut
  `null` sur le web.
- **`lib/auth_errors.dart`** (nouveau) : traduit en français les erreurs Auth
  courantes (rate limit, compte existant, mot de passe faible, email non
  confirmé, identifiants invalides, inscriptions désactivées).
- **`lib/screens/register_page.dart`**
  - `signUp(..., emailRedirectTo: SupabaseConfig.authRedirectUrl)`.
  - Détection d'un email déjà inscrit (`user.identities` vide).
  - Message clair si la session est `null` : ouvrir le lien reçu **sur ce
    téléphone**. Le flux PKCE exige le même appareil.
  - Affichage de la vraie erreur au lieu de « Erreur réseau ».
- **`lib/screens/login_page.dart`**
  - Erreur « Email non confirmé » avec un bouton **Renvoyer**
    (`auth.resend(type: OtpType.signup, ...)`).
  - `resetPasswordForEmail` utilise aussi le deep link.
- **`android/app/src/main/AndroidManifest.xml`** : permission `INTERNET` et
  `intent-filter` pour le schéma `io.supabase.smartapp://login-callback`.
- **`ios/Runner/Info.plist`** : `CFBundleURLTypes` avec le schéma
  `io.supabase.smartapp`.

Quand le lien est ouvert, `supabase_flutter` récupère la session à partir du deep
link. `AuthGate` reçoit l'événement `onAuthStateChange` et affiche `HomePage`.

## ⚠️ À faire dans le tableau de bord Supabase (obligatoire)

Ces réglages ne se font pas dans le code :

1. **Authentication → URL Configuration → Redirect URLs** : ajouter
   `io.supabase.smartapp://login-callback/`.
   Sans cela, Supabase ignore `emailRedirectTo` et renvoie vers la Site URL.
2. *(Web uniquement)* Mettre la **Site URL** sur l'URL où tourne l'app web,
   par exemple `http://localhost:XXXX`.
3. **Limite d'emails** : le service SMTP intégré de Supabase n'envoie que
   **quelques emails par heure** et seulement aux adresses de l'équipe du
   projet. Pour les tests, deux options :
   - **Option rapide (développement)** : Authentication → Sign In / Providers →
     Email → désactiver **Confirm email**. `signUp()` renvoie alors directement
     une session et l'utilisateur arrive sur `HomePage`. Le code gère les deux
     cas.
   - **Option propre** : configurer un SMTP personnalisé (Authentication →
     Emails → SMTP Settings), par exemple Resend, Brevo ou Gmail.

## Comment tester

```bash
flutter clean
flutter pub get
flutter run
```

1. Créer un compte avec un **nouvel** email : un message indique d'ouvrir le
   lien reçu.
2. Ouvrir l'email **sur le téléphone ou l'émulateur** et cliquer le lien :
   l'app s'ouvre directement sur `HomePage`.
3. Réessayer avec le même email : « Un compte existe déjà avec cet email ».
4. Se connecter avant confirmation : « Email non confirmé » avec le bouton
   **Renvoyer**.

> Après une modification de `AndroidManifest.xml` ou de `Info.plist`, il faut
> **relancer complètement l'app**. Un hot reload ne suffit pas.

## Configuration appliquée (23/11/2026, via l'API Management Supabase)

| Réglage | Avant | Après |
|---------|-------|-------|
| `mailer_autoconfirm` (Confirm email désactivé) | `false` | `true` |
| `uri_allow_list` (Redirect URLs) | *(vide)* | `io.supabase.smartapp://login-callback/`, `http://localhost:3000/**` |
| `site_url` | `http://localhost:3000` | inchangé |

Vérifié avec `GET /auth/v1/settings` : `"mailer_autoconfirm": true`.
L'inscription n'envoie plus d'email et ouvre directement `HomePage`.

Pour réactiver la confirmation plus tard, il faut d'abord configurer un SMTP
personnalisé, puis remettre `mailer_autoconfirm` à `false`.
