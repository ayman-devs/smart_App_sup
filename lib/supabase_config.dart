import 'package:flutter/foundation.dart' show kIsWeb;

// Configuration publique : remplacez ces exemples par votre projet.
// Ne jamais placer de secret key ou service_role dans Flutter.
class SupabaseConfig {
  static const String url = 'https://jduepkhccwqisasrcdce.supabase.co';
  static const String publishableKey = 'sb_publishable_EGLBnPUW7nrFfptvqyJFWQ_Uw9NHfq9';

  /// Deep link ouvert par le lien de confirmation email.
  /// Doit aussi être ajouté dans Supabase > Authentication > URL Configuration
  /// > Redirect URLs.
  static const String mobileRedirectUrl = 'io.supabase.smartapp://login-callback/';

  /// Sur le web on laisse Supabase utiliser la Site URL / l'URL courante.
  static String? get authRedirectUrl => kIsWeb ? null : mobileRedirectUrl;
}
