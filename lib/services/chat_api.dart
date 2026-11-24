import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import '../backend_config.dart';

/// A page cited by the backend.
class SourcePage {
  SourcePage({required this.source, required this.page});

  final String source;
  final int page;

  factory SourcePage.fromJson(Map<String, dynamic> json) => SourcePage(
    source: json['source']?.toString() ?? '',
    page: (json['page'] as num?)?.toInt() ?? 0,
  );
}

/// What POST /chat returns.
class ChatAnswer {
  ChatAnswer({
    required this.answer,
    required this.sources,
    required this.imageUrls,
    required this.toolCalls,
    required this.agents,
    required this.seconds,
  });

  final String answer;
  final List<SourcePage> sources;
  final List<String> imageUrls; // full URLs, ready for Image.network
  final List<String> toolCalls; // agent mode: names of the tools used
  final List<String> agents; // multi mode: "Superviseur", "Document"...
  final double seconds;

  factory ChatAnswer.fromJson(Map<String, dynamic> json) {
    final images = (json['images'] as List? ?? const [])
        .map((i) => '${BackendConfig.baseUrl}${(i as Map)['url']}')
        .toList();
    return ChatAnswer(
      answer: json['answer']?.toString() ?? '',
      sources: (json['sources'] as List? ?? const [])
          .map((s) => SourcePage.fromJson(Map<String, dynamic>.from(s as Map)))
          .toList(),
      imageUrls: images,
      toolCalls: (json['steps'] as List? ?? const [])
          .map((s) => (s as Map)['tool'].toString())
          .toList(),
      agents: (json['agents'] as List? ?? const [])
          .map((a) => a.toString())
          .toList(),
      seconds: (json['seconds'] as num?)?.toDouble() ?? 0,
    );
  }

  /// Same shape as the backend's answer, to save it in the history.
  /// Image URLs are saved WITHOUT the server address (it differs between
  /// web and Android emulator); fromJson() adds it back.
  Map<String, dynamic> toJson() => {
    'answer': answer,
    'sources': [
      for (final s in sources) {'source': s.source, 'page': s.page},
    ],
    'images': [
      for (final url in imageUrls)
        {'url': url.replaceFirst(BackendConfig.baseUrl, '')},
    ],
    'steps': [
      for (final t in toolCalls) {'tool': t},
    ],
    'agents': agents,
    'seconds': seconds,
  };
}

/// Error with a message we can show as is to the user.
class ChatApiException implements Exception {
  ChatApiException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// Talks to the Python RAG backend.
class ChatApi {
  ChatApi({http.Client? client}) : _client = client ?? http.Client();

  final http.Client _client;

  Uri _uri(String path) => Uri.parse('${BackendConfig.baseUrl}$path');

  /// Sends a question (and an optional photo) to POST /chat.
  ///
  /// `history` = the previous messages of the conversation
  /// ([{'role': 'user'|'assistant', 'content': ...}]). The backend uses it
  /// only if its memory of this conversation is empty (backend restarted,
  /// or a chat reopened from the history).
  Future<ChatAnswer> ask({
    required String question,
    Uint8List? photo,
    String mode = 'classic', // 'classic', 'agent' or 'multi'
    String? conversationId,
    List<Map<String, String>> history = const [],
  }) async {
    final body = jsonEncode({
      'question': question,
      'image_base64': photo == null ? null : base64Encode(photo),
      'mode': mode,
      'conversation_id': conversationId,
      'history': history,
    });

    final http.Response response;
    try {
      response = await _client
          .post(
            _uri('/chat'),
            headers: {'Content-Type': 'application/json'},
            body: body,
          )
          .timeout(BackendConfig.timeout);
    } on TimeoutException {
      throw ChatApiException(
        'Le serveur met trop de temps à répondre. Réessayez.',
      );
    } catch (_) {
      // On web and mobile, a stopped server ends up here.
      throw ChatApiException(
        'Impossible de joindre le serveur (${BackendConfig.baseUrl}).\n'
        'Le backend est-il lancé ? → uvicorn app.main:app --port 8000',
      );
    }

    final data = _decode(response);
    if (response.statusCode != 200) {
      throw ChatApiException(
        'Erreur du serveur (${response.statusCode}) : ${data['detail'] ?? response.body}',
      );
    }
    return ChatAnswer.fromJson(data);
  }

  /// Clears the agent's memory for one conversation.
  Future<void> resetConversation(String conversationId) async {
    try {
      await _client.post(
        _uri('/agent/reset'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'conversation_id': conversationId}),
      );
    } catch (_) {
      // Not important: a new conversation id is used anyway.
    }
  }

  Map<String, dynamic> _decode(http.Response response) {
    try {
      // utf8.decode: keeps the French accents correct.
      return Map<String, dynamic>.from(
        jsonDecode(utf8.decode(response.bodyBytes)) as Map,
      );
    } catch (_) {
      return {'detail': response.body};
    }
  }
}
