import 'dart:typed_data';

import 'package:supabase_flutter/supabase_flutter.dart';

import 'chat_api.dart';

/// A saved conversation, as shown in the "Historique" list.
class ConversationSummary {
  ConversationSummary({
    required this.id,
    required this.title,
    required this.updatedAt,
  });

  final String id;
  final String title; // the first question
  final DateTime updatedAt;

  factory ConversationSummary.fromJson(Map<String, dynamic> json) =>
      ConversationSummary(
        id: json['id'] as String,
        title: json['title'] as String,
        updatedAt: DateTime.parse(json['updated_at'] as String).toLocal(),
      );
}

/// A saved message, read back from Supabase.
class StoredMessage {
  StoredMessage({
    required this.fromUser,
    required this.content,
    this.photo,
    this.answer,
  });

  final bool fromUser;
  final String content;
  final Uint8List? photo; // user's photo, downloaded from Storage
  final ChatAnswer? answer; // assistant: pages, image, agents, tools
}

/// Saves the chats in Supabase (tables `conversations` and `chat_messages`,
/// photos in the private bucket `chat-photos`).
///
/// Security is done by the database (Row Level Security): each user can
/// only read and write their own rows, so no user id is checked here.
/// The SQL is in supabase/migrations/20261124_chat_history.sql.
class ChatHistory {
  SupabaseClient get _db => Supabase.instance.client;

  static const _photoBucket = 'chat-photos';

  /// The user's conversations, most recent first.
  Future<List<ConversationSummary>> listConversations() async {
    final rows = await _db
        .from('conversations')
        .select('id, title, updated_at')
        .order('updated_at', ascending: false);
    return rows.map(ConversationSummary.fromJson).toList();
  }

  /// Creates a conversation; its id is also the agent's conversation_id.
  Future<String> createConversation(String firstQuestion) async {
    final title = firstQuestion.length > 60
        ? '${firstQuestion.substring(0, 60)}…'
        : firstQuestion;
    final row = await _db
        .from('conversations')
        .insert({'title': title})
        .select('id')
        .single();
    return row['id'] as String;
  }

  /// Saves the user's question (and uploads their photo, if any).
  Future<void> saveUserMessage(
    String conversationId,
    String text, {
    Uint8List? photo,
  }) async {
    String? photoPath;
    if (photo != null) {
      // One folder per user: the Storage policy only allows "<user id>/...".
      final userId = _db.auth.currentUser!.id;
      photoPath =
          '$userId/$conversationId/${DateTime.now().millisecondsSinceEpoch}.jpg';
      await _db.storage
          .from(_photoBucket)
          .uploadBinary(
            photoPath,
            photo,
            fileOptions: const FileOptions(contentType: 'image/jpeg'),
          );
    }
    await _db.from('chat_messages').insert({
      'conversation_id': conversationId,
      'role': 'user',
      'content': text,
      'photo_path': photoPath,
    });
  }

  /// Saves the assistant's answer, and moves the conversation to the top.
  Future<void> saveAnswer(String conversationId, ChatAnswer answer) async {
    await _db.from('chat_messages').insert({
      'conversation_id': conversationId,
      'role': 'assistant',
      'content': answer.answer,
      'details': answer.toJson(),
    });
    await _db
        .from('conversations')
        .update({'updated_at': DateTime.now().toUtc().toIso8601String()})
        .eq('id', conversationId);
  }

  /// All the messages of one conversation, in order, with their photos.
  Future<List<StoredMessage>> loadMessages(String conversationId) async {
    final rows = await _db
        .from('chat_messages')
        .select('role, content, photo_path, details')
        .eq('conversation_id', conversationId)
        .order('id');
    // Download the photos in parallel (the bucket is private).
    return Future.wait(
      rows.map((row) async {
        final fromUser = row['role'] == 'user';
        final path = row['photo_path'] as String?;
        Uint8List? photo;
        if (path != null) {
          try {
            photo = await _db.storage.from(_photoBucket).download(path);
          } catch (_) {
            photo = null; // photo deleted: show the text only
          }
        }
        final details = row['details'];
        return StoredMessage(
          fromUser: fromUser,
          content: row['content'] as String,
          photo: photo,
          answer: !fromUser && details is Map
              ? ChatAnswer.fromJson(Map<String, dynamic>.from(details))
              : null,
        );
      }),
    );
  }
}
