import 'dart:math' as math;

import 'package:flutter/material.dart';

/// Palette verte inspirée de l'identité de l'EMSI.
class EmsiColors {
  static const green = Color(0xFF00843D);
  static const greenDark = Color(0xFF005C2B);
  static const greenDeep = Color(0xFF0B2E1C);
  static const greenLight = Color(0xFF3DAE6B);
  static const error = Color(0xFFFFE082); // lisible sur fond vert
}

/// Gabarit commun des pages Connexion / Inscription :
/// - grand écran : formulaire vert à gauche + panneau décoratif à droite ;
/// - mobile : formulaire vert plein écran.
class AuthLayout extends StatelessWidget {
  const AuthLayout({
    super.key,
    required this.title,
    required this.subtitle,
    required this.child,
    this.showBack = false,
  });

  final String title;
  final String subtitle;
  final Widget child;
  final bool showBack;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: EmsiColors.green,
      body: LayoutBuilder(
        builder: (context, constraints) {
          final wide = constraints.maxWidth >= 900;
          final form = _FormPanel(
            title: title,
            subtitle: subtitle,
            showBack: showBack,
            child: child,
          );
          if (!wide) return form;
          return Row(
            children: [
              Expanded(flex: 5, child: form),
              const Expanded(flex: 6, child: _DecorPanel()),
            ],
          );
        },
      ),
    );
  }
}

class _FormPanel extends StatelessWidget {
  const _FormPanel({
    required this.title,
    required this.subtitle,
    required this.child,
    required this.showBack,
  });

  final String title;
  final String subtitle;
  final Widget child;
  final bool showBack;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: const BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [EmsiColors.green, EmsiColors.greenDark],
        ),
      ),
      child: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(horizontal: 32, vertical: 28),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(
                    children: [
                      if (showBack)
                        Padding(
                          padding: const EdgeInsets.only(right: 8),
                          child: IconButton(
                            tooltip: 'Retour',
                            onPressed: () => Navigator.maybePop(context),
                            icon: const Icon(Icons.arrow_back,
                                color: Colors.white),
                          ),
                        ),
                      const _Logo(),
                    ],
                  ),
                  const SizedBox(height: 44),
                  Text(
                    title,
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 28,
                      fontWeight: FontWeight.w800,
                      height: 1.2,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    subtitle,
                    style: TextStyle(
                      color: Colors.white.withValues(alpha: 0.78),
                      fontSize: 15,
                    ),
                  ),
                  const SizedBox(height: 32),
                  child,
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _Logo extends StatelessWidget {
  const _Logo();

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 44,
          height: 44,
          decoration: BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.circular(12),
          ),
          child: const Icon(Icons.school_rounded,
              color: EmsiColors.green, size: 26),
        ),
        const SizedBox(width: 12),
        Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Smart_App',
              style: TextStyle(
                color: Colors.white,
                fontSize: 20,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.3,
              ),
            ),
            Text(
              'EMSI',
              style: TextStyle(
                color: Colors.white.withValues(alpha: 0.7),
                fontSize: 12,
                letterSpacing: 2,
                fontWeight: FontWeight.w600,
              ),
            ),
          ],
        ),
      ],
    );
  }
}

/// Panneau de droite : dégradé vert foncé + carrés concentriques et
/// cercles lumineux (clin d'œil à la photo d'escalier de la maquette).
class _DecorPanel extends StatelessWidget {
  const _DecorPanel();

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: const BorderRadius.only(
        topLeft: Radius.circular(40),
        bottomLeft: Radius.circular(40),
      ),
      child: Container(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topRight,
            end: Alignment.bottomLeft,
            colors: [EmsiColors.greenDeep, EmsiColors.greenDark],
          ),
        ),
        child: Stack(
          fit: StackFit.expand,
          children: [
            CustomPaint(painter: _SquaresPainter()),
            Positioned(
              left: 48,
              right: 48,
              bottom: 56,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Apprendre, créer,\nconnecter.',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 34,
                      fontWeight: FontWeight.w800,
                      height: 1.15,
                    ),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    'Votre espace Smart_App, sécurisé par Supabase.',
                    style: TextStyle(
                      color: Colors.white.withValues(alpha: 0.75),
                      fontSize: 16,
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _SquaresPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width * 0.55, size.height * 0.42);
    final maxSide = math.min(size.width, size.height) * 1.1;

    // Carrés concentriques qui tournent légèrement : effet de profondeur.
    for (var i = 0; i < 12; i++) {
      final t = i / 12;
      final side = maxSide * (1 - t * 0.9);
      final paint = Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2
        ..color = (i.isEven ? EmsiColors.greenLight : Colors.white)
            .withValues(alpha: 0.10 + t * 0.25);
      canvas.save();
      canvas.translate(center.dx, center.dy);
      canvas.rotate(t * 0.35);
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromCenter(center: Offset.zero, width: side, height: side),
          const Radius.circular(6),
        ),
        paint,
      );
      canvas.restore();
    }

    // Lignes qui convergent vers le centre.
    final line = Paint()
      ..color = Colors.white.withValues(alpha: 0.08)
      ..strokeWidth = 1.2;
    for (final p in [
      Offset.zero,
      Offset(size.width, 0),
      Offset(0, size.height),
      Offset(size.width, size.height),
      Offset(size.width * 0.3, 0),
      Offset(size.width * 0.8, 0),
    ]) {
      canvas.drawLine(p, center, line);
    }

    // Cercles lumineux.
    final glow = Paint()
      ..color = Colors.white.withValues(alpha: 0.9)
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 6);
    final dot = Paint()..color = EmsiColors.greenDeep.withValues(alpha: 0.7);
    for (final (dx, dy, r) in [
      (0.62, 0.16, 46.0),
      (0.40, 0.48, 18.0),
      (0.72, 0.52, 26.0),
      (0.52, 0.58, 10.0),
      (0.58, 0.46, 8.0),
    ]) {
      final c = Offset(size.width * dx, size.height * dy);
      canvas.drawCircle(c, r, glow);
      canvas.drawCircle(c, r * 0.32, dot);
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

/// Libellé au-dessus du champ + champ blanc arrondi (comme la maquette).
class AuthTextField extends StatelessWidget {
  const AuthTextField({
    super.key,
    required this.controller,
    required this.label,
    required this.hint,
    required this.icon,
    this.obscure = false,
    this.suffix,
    this.validator,
    this.keyboardType,
    this.autofillHints,
    this.textInputAction,
    this.onSubmitted,
  });

  final TextEditingController controller;
  final String label;
  final String hint;
  final IconData icon;
  final bool obscure;
  final Widget? suffix;
  final String? Function(String?)? validator;
  final TextInputType? keyboardType;
  final Iterable<String>? autofillHints;
  final TextInputAction? textInputAction;
  final ValueChanged<String>? onSubmitted;

  @override
  Widget build(BuildContext context) {
    OutlineInputBorder border(Color color, [double width = 1]) =>
        OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide(color: color, width: width),
        );

    return Padding(
      padding: const EdgeInsets.only(bottom: 18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: const TextStyle(
              color: Colors.white,
              fontWeight: FontWeight.w600,
              fontSize: 14,
            ),
          ),
          const SizedBox(height: 8),
          TextFormField(
            controller: controller,
            obscureText: obscure,
            keyboardType: keyboardType,
            autofillHints: autofillHints,
            textInputAction: textInputAction,
            onFieldSubmitted: onSubmitted,
            validator: validator,
            cursorColor: EmsiColors.green,
            style: const TextStyle(color: Color(0xFF1B1B1B)),
            decoration: InputDecoration(
              hintText: hint,
              hintStyle: const TextStyle(color: Color(0xFF8A8F8C)),
              prefixIcon: Icon(icon, color: EmsiColors.green),
              suffixIcon: suffix,
              filled: true,
              fillColor: Colors.white,
              contentPadding:
                  const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
              border: border(Colors.transparent),
              enabledBorder: border(Colors.transparent),
              focusedBorder: border(EmsiColors.greenLight, 2),
              errorBorder: border(EmsiColors.error, 2),
              focusedErrorBorder: border(EmsiColors.error, 2),
              errorStyle: const TextStyle(
                color: EmsiColors.error,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// Bouton principal blanc à texte vert.
class AuthPrimaryButton extends StatelessWidget {
  const AuthPrimaryButton({
    super.key,
    required this.label,
    required this.loading,
    required this.onPressed,
  });

  final String label;
  final bool loading;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 54,
      child: FilledButton(
        onPressed: loading ? null : onPressed,
        style: FilledButton.styleFrom(
          backgroundColor: Colors.white,
          foregroundColor: EmsiColors.green,
          disabledBackgroundColor: Colors.white.withValues(alpha: 0.8),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
          textStyle: const TextStyle(
            fontSize: 16,
            fontWeight: FontWeight.w700,
          ),
        ),
        child: loading
            ? const SizedBox(
                width: 22,
                height: 22,
                child: CircularProgressIndicator(
                  strokeWidth: 2.5,
                  color: EmsiColors.green,
                ),
              )
            : Text(label),
      ),
    );
  }
}

/// Ligne « Pas de compte ? S'inscrire ».
class AuthSwitchLink extends StatelessWidget {
  const AuthSwitchLink({
    super.key,
    required this.question,
    required this.action,
    required this.onTap,
  });

  final String question;
  final String action;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        Text(
          question,
          style: TextStyle(color: Colors.white.withValues(alpha: 0.8)),
        ),
        TextButton(
          onPressed: onTap,
          style: TextButton.styleFrom(foregroundColor: Colors.white),
          child: Text(
            action,
            style: const TextStyle(
              fontWeight: FontWeight.w700,
              decoration: TextDecoration.underline,
              decorationColor: Colors.white,
            ),
          ),
        ),
      ],
    );
  }
}
