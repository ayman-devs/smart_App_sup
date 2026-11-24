import 'package:flutter/foundation.dart';

/// Single place for the RAG backend's address.
///
/// - Web / Windows / iOS simulator: the backend runs on the same computer
///   → http://localhost:8000
/// - Android emulator: "localhost" means the emulator itself;
///   10.0.2.2 is the emulator's alias for your computer.
/// - Real phone: run with your PC's local IP, for example
///   flutter run --dart-define=BACKEND_URL=http://192.168.1.20:8000
class BackendConfig {
  static const String _override = String.fromEnvironment('BACKEND_URL');

  static String get baseUrl {
    if (_override.isNotEmpty) return _override;
    if (kIsWeb) return 'http://localhost:8000';
    if (defaultTargetPlatform == TargetPlatform.android) {
      return 'http://10.0.2.2:8000';
    }
    return 'http://localhost:8000';
  }

  /// The agent may call the model several times: allow a long wait.
  static const Duration timeout = Duration(minutes: 5);
}
