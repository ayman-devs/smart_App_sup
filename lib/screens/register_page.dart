import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../auth_errors.dart';
import '../supabase_config.dart';
import '../widgets/auth_layout.dart';

class RegisterPage extends StatefulWidget {
  const RegisterPage({super.key});

  @override
  State<RegisterPage> createState() => _RegisterPageState();
}

class _RegisterPageState extends State<RegisterPage> {
  final _formKey = GlobalKey<FormState>();
  final _name = TextEditingController();
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _confirm = TextEditingController();
  bool _loading = false;
  bool _visible = false;

  @override
  void dispose() {
    _name.dispose();
    _email.dispose();
    _password.dispose();
    _confirm.dispose();
    super.dispose();
  }

  void _message(String text, {Duration duration = const Duration(seconds: 4)}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(text), duration: duration),
    );
  }

  Future<void> _register() async {
    if (!_formKey.currentState!.validate()) return;
    FocusScope.of(context).unfocus();
    setState(() => _loading = true);
    try {
      final result = await Supabase.instance.client.auth.signUp(
        email: _email.text.trim(),
        password: _password.text,
        data: {'display_name': _name.text.trim()},
        // Sans cela, le lien de confirmation renvoie vers la Site URL
        // (par défaut http://localhost:3000) et ne rouvre pas l'app.
        emailRedirectTo: SupabaseConfig.authRedirectUrl,
      );
      if (!mounted) return;

      final user = result.user;
      // Quand la confirmation email est activée, Supabase ne renvoie PAS
      // d'erreur pour un email déjà inscrit : il renvoie un faux utilisateur
      // sans identité (protection contre l'énumération des comptes).
      if (user != null && (user.identities?.isEmpty ?? false)) {
        _message('Un compte existe déjà avec cet email. Connectez-vous.');
        return;
      }

      if (result.session == null) {
        // Confirmation email activée : l'utilisateur n'est pas encore connecté.
        _message(
          'Compte créé ! Ouvrez le lien envoyé à ${_email.text.trim()} '
          'sur ce téléphone pour activer votre compte.',
          duration: const Duration(seconds: 8),
        );
      }
      // Session présente : AuthGate affiche déjà HomePage sous cette page.
      Navigator.pop(context);
    } on AuthException catch (e) {
      _message('Inscription impossible : ${authErrorMessage(e)}');
    } catch (e) {
      debugPrint('Erreur inscription : $e');
      _message('Erreur pendant l’inscription : $e');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return AuthLayout(
      title: 'Créer votre compte',
      subtitle: 'Rejoignez Smart_App en quelques secondes.',
      showBack: true,
      child: AutofillGroup(
        child: Form(
          key: _formKey,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              AuthTextField(
                controller: _name,
                label: 'Nom affiché',
                hint: 'Votre nom',
                icon: Icons.person_outline,
                autofillHints: const [AutofillHints.name],
                textInputAction: TextInputAction.next,
                validator: (v) => v == null || v.trim().isEmpty
                    ? 'Indiquez votre nom'
                    : null,
              ),
              AuthTextField(
                controller: _email,
                label: 'Email',
                hint: 'prenom.nom@exemple.com',
                icon: Icons.email_outlined,
                keyboardType: TextInputType.emailAddress,
                autofillHints: const [AutofillHints.email],
                textInputAction: TextInputAction.next,
                validator: (v) => v == null ||
                        !RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$').hasMatch(v.trim())
                    ? 'Email invalide'
                    : null,
              ),
              AuthTextField(
                controller: _password,
                label: 'Mot de passe',
                hint: '6 caractères minimum',
                icon: Icons.lock_outline,
                obscure: !_visible,
                autofillHints: const [AutofillHints.newPassword],
                textInputAction: TextInputAction.next,
                suffix: IconButton(
                  onPressed: () => setState(() => _visible = !_visible),
                  icon: Icon(
                    _visible ? Icons.visibility : Icons.visibility_off,
                    color: EmsiColors.green,
                  ),
                ),
                validator: (v) =>
                    v == null || v.length < 6 ? 'Au moins 6 caractères' : null,
              ),
              AuthTextField(
                controller: _confirm,
                label: 'Confirmer le mot de passe',
                hint: 'Retapez le mot de passe',
                icon: Icons.lock_reset_outlined,
                obscure: !_visible,
                textInputAction: TextInputAction.done,
                onSubmitted: (_) => _loading ? null : _register(),
                validator: (v) => v != _password.text
                    ? 'Les mots de passe ne correspondent pas'
                    : null,
              ),
              const SizedBox(height: 12),
              AuthPrimaryButton(
                label: 'Créer mon compte',
                loading: _loading,
                onPressed: _register,
              ),
              const SizedBox(height: 20),
              AuthSwitchLink(
                question: 'Déjà un compte ?',
                action: 'Se connecter',
                onTap: _loading ? null : () => Navigator.maybePop(context),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
