import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../auth_errors.dart';
import '../supabase_config.dart';
import '../widgets/auth_layout.dart';

class LoginPage extends StatefulWidget {
  const LoginPage({super.key});

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final _formKey = GlobalKey<FormState>();
  final _emailController = TextEditingController();
  final _passwordController = TextEditingController();
  bool _passwordVisible = false;
  bool _loading = false;

  @override
  void dispose() {
    _emailController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  void _message(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message)),
    );
  }

  Future<void> _login() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() => _loading = true);
    try {
      await Supabase.instance.client.auth.signInWithPassword(
        email: _emailController.text.trim(),
        password: _passwordController.text,
      );
      // AuthGate affiche automatiquement HomePage.
    } on AuthException catch (e) {
      final notConfirmed = e.code == 'email_not_confirmed' ||
          e.message.toLowerCase().contains('email not confirmed');
      if (notConfirmed && mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(authErrorMessage(e)),
          duration: const Duration(seconds: 8),
          action: SnackBarAction(
            label: 'Renvoyer',
            onPressed: _resendConfirmation,
          ),
        ));
      } else {
        _message('Connexion impossible : ${authErrorMessage(e)}');
      }
    } catch (e) {
      debugPrint('Erreur connexion : $e');
      _message('Erreur de connexion. Vérifiez votre réseau.');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _resendConfirmation() async {
    try {
      await Supabase.instance.client.auth.resend(
        type: OtpType.signup,
        email: _emailController.text.trim(),
        emailRedirectTo: SupabaseConfig.authRedirectUrl,
      );
      _message('Email de confirmation renvoyé.');
    } on AuthException catch (e) {
      _message('Envoi impossible : ${authErrorMessage(e)}');
    } catch (_) {
      _message('Envoi impossible pour le moment.');
    }
  }

  Future<void> _forgotPassword() async {
    final email = _emailController.text.trim();
    if (!RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$').hasMatch(email)) {
      _message('Saisissez un email valide avant de réinitialiser le mot de passe.');
      return;
    }
    try {
      await Supabase.instance.client.auth.resetPasswordForEmail(
        email,
        redirectTo: SupabaseConfig.authRedirectUrl,
      );
      _message('Si ce compte existe, un email de récupération sera envoyé.');
    } on AuthException catch (e) {
      _message('Erreur : ${authErrorMessage(e)}');
    } catch (_) {
      _message('Envoi impossible pour le moment.');
    }
  }

  @override
  Widget build(BuildContext context) {
    return AuthLayout(
      title: 'Heureux de vous revoir',
      subtitle: 'Connectez-vous pour continuer.',
      child: AutofillGroup(
        child: Form(
          key: _formKey,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              AuthTextField(
                controller: _emailController,
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
                controller: _passwordController,
                label: 'Mot de passe',
                hint: 'Votre mot de passe',
                icon: Icons.lock_outline,
                obscure: !_passwordVisible,
                autofillHints: const [AutofillHints.password],
                textInputAction: TextInputAction.done,
                onSubmitted: (_) => _loading ? null : _login(),
                suffix: IconButton(
                  onPressed: () =>
                      setState(() => _passwordVisible = !_passwordVisible),
                  icon: Icon(
                    _passwordVisible ? Icons.visibility : Icons.visibility_off,
                    color: EmsiColors.green,
                  ),
                ),
                validator: (v) => (v == null || v.isEmpty)
                    ? 'Mot de passe obligatoire'
                    : null,
              ),
              Align(
                alignment: Alignment.centerRight,
                child: TextButton(
                  onPressed: _loading ? null : _forgotPassword,
                  style: TextButton.styleFrom(foregroundColor: Colors.white),
                  child: const Text('Mot de passe oublié ?'),
                ),
              ),
              const SizedBox(height: 16),
              AuthPrimaryButton(
                label: 'Se connecter',
                loading: _loading,
                onPressed: _login,
              ),
              const SizedBox(height: 20),
              AuthSwitchLink(
                question: 'Pas encore de compte ?',
                action: 'S’inscrire',
                onTap: _loading
                    ? null
                    : () => Navigator.pushNamed(context, '/register'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
