import 'package:supabase_flutter/supabase_flutter.dart';

/// Traduit les erreurs Supabase Auth les plus fréquentes en français.
String authErrorMessage(AuthException e) {
  final msg = e.message.toLowerCase();
  final code = e.code ?? '';
  if (code == 'over_email_send_rate_limit' || msg.contains('rate limit')) {
    return 'Trop d’emails envoyés. Le service email par défaut de Supabase '
        'est limité : réessayez plus tard ou configurez un SMTP.';
  }
  if (code == 'user_already_exists' || msg.contains('already registered')) {
    return 'Un compte existe déjà avec cet email. Connectez-vous.';
  }
  if (code == 'weak_password' || msg.contains('password should')) {
    return 'Mot de passe trop faible : ${e.message}';
  }
  if (code == 'email_not_confirmed' || msg.contains('email not confirmed')) {
    return 'Email non confirmé. Cliquez sur le lien reçu par email.';
  }
  if (code == 'invalid_credentials' || msg.contains('invalid login')) {
    return 'Email ou mot de passe incorrect.';
  }
  if (code == 'signup_disabled' || msg.contains('signups not allowed')) {
    return 'Les inscriptions sont désactivées sur ce projet Supabase.';
  }
  if (code == 'email_address_invalid' || msg.contains('is invalid')) {
    return 'Adresse email refusée par Supabase : utilisez une vraie adresse '
        'qui existe (les adresses factices sont bloquées).';
  }
  return e.message;
}
