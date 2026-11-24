import 'dart:math';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../services/chat_api.dart';
import '../services/chat_history.dart';
import '../widgets/auth_layout.dart' show EmsiColors;

/// One message in the conversation.
class ChatMessage {
  ChatMessage.user(this.text, {this.photo})
    : fromUser = true,
      isError = false,
      answer = null;

  /// A message read back from the history (Supabase).
  ChatMessage.stored(StoredMessage m)
    : fromUser = m.fromUser,
      isError = false,
      text = m.content,
      photo = m.photo,
      answer = m.answer;

  ChatMessage.assistant(ChatAnswer this.answer)
    : fromUser = false,
      isError = false,
      text = answer.answer,
      photo = null;

  ChatMessage.error(this.text)
    : fromUser = false,
      isError = true,
      answer = null,
      photo = null;

  final bool fromUser;
  final bool isError;
  final String text;
  final Uint8List? photo; // photo attached by the user
  final ChatAnswer? answer; // assistant extras: pages, image, tools
}

class ChatPage extends StatefulWidget {
  const ChatPage({super.key});

  @override
  State<ChatPage> createState() => _ChatPageState();
}

class _ChatPageState extends State<ChatPage> {
  final _api = ChatApi();
  final _history = ChatHistory();
  final _scaffoldKey = GlobalKey<ScaffoldState>();
  final _picker = ImagePicker();
  final _input = TextEditingController();
  final _scroll = ScrollController();
  final List<ChatMessage> _messages = [];

  Uint8List? _photo; // photo waiting to be sent
  bool _loading = false; // waiting for an answer
  bool _opening = false; // loading a conversation from the history
  String _mode = 'classic'; // 'classic', 'agent' or 'multi'

  // Id of the conversation = its id in Supabase, also sent to the backend as
  // conversation_id (agent memory). null = new chat, not saved yet: it is
  // created in Supabase when the first question is sent.
  String? _conversationId;
  bool _saving = true; // false if Supabase failed: the chat still works

  static String _localConversationId() =>
      'local_${DateTime.now().millisecondsSinceEpoch}_${Random().nextInt(99999)}';

  @override
  void dispose() {
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }

  // ------------------------------------------------------------ actions

  Future<void> _pickPhoto(ImageSource source) async {
    try {
      // maxWidth + quality: smaller upload, faster answer.
      final file = await _picker.pickImage(
        source: source,
        maxWidth: 1280,
        imageQuality: 80,
      );
      if (file == null) return; // user cancelled
      final bytes = await file.readAsBytes();
      setState(() => _photo = bytes);
    } catch (e) {
      _snack(
        'Impossible d’ouvrir ${source == ImageSource.camera ? 'la caméra' : 'la galerie'}.',
      );
    }
  }

  void _choosePhotoSource() {
    showModalBottomSheet<void>(
      context: context,
      builder: (context) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.photo_library_outlined),
              title: const Text('Galerie'),
              onTap: () {
                Navigator.pop(context);
                _pickPhoto(ImageSource.gallery);
              },
            ),
            ListTile(
              leading: const Icon(Icons.photo_camera_outlined),
              title: const Text('Caméra'),
              onTap: () {
                Navigator.pop(context);
                _pickPhoto(ImageSource.camera);
              },
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _send() async {
    final question = _input.text.trim();
    if (_loading) return;
    if (question.isEmpty && _photo == null) return;

    final photo = _photo;
    // A photo alone still needs a question for the backend.
    final text = question.isEmpty ? 'Que montre cette image ?' : question;

    // The conversation so far (errors excluded), for the agent's memory.
    final history = [
      for (final m in _messages)
        if (!m.isError)
          {'role': m.fromUser ? 'user' : 'assistant', 'content': m.text},
    ];

    setState(() {
      _messages.add(ChatMessage.user(text, photo: photo));
      _input.clear();
      _photo = null;
      _loading = true;
    });
    _scrollToBottom();

    // 1. Save the question in Supabase (the first one creates the chat).
    final conversationId = await _ensureConversation(text);
    final savingQuestion = _save(
      () => _history.saveUserMessage(conversationId, text, photo: photo),
    );

    try {
      // 2. Ask the backend (while the question is being saved).
      final answer = await _api.ask(
        question: text,
        photo: photo,
        mode: _mode,
        conversationId: conversationId,
        history: history,
      );
      if (!mounted) return;
      setState(() => _messages.add(ChatMessage.assistant(answer)));
      // 3. Save the answer, after the question (keeps the order).
      await savingQuestion;
      await _save(() => _history.saveAnswer(conversationId, answer));
    } on ChatApiException catch (e) {
      // Errors are shown but not saved in the history.
      if (!mounted) return;
      setState(() => _messages.add(ChatMessage.error(e.message)));
    } finally {
      if (mounted) setState(() => _loading = false);
      _scrollToBottom();
    }
  }

  /// The id of the current chat; creates it in Supabase on the first question.
  Future<String> _ensureConversation(String firstQuestion) async {
    if (_conversationId != null) return _conversationId!;
    String id;
    try {
      id = await _history.createConversation(firstQuestion);
    } catch (_) {
      // No history (e.g. no internet to Supabase): the chat still works.
      id = _localConversationId();
      _saving = false;
      _snack(
        'Historique indisponible : cette conversation ne sera pas enregistrée.',
      );
    }
    _conversationId = id;
    return id;
  }

  /// Runs a save; a failure must never break the chat.
  Future<void> _save(Future<void> Function() action) async {
    if (!_saving) return;
    try {
      await action();
    } catch (_) {
      _saving = false;
      _snack('Erreur d’enregistrement : la suite ne sera pas enregistrée.');
    }
  }

  void _newConversation() {
    setState(() {
      _messages.clear();
      _conversationId = null; // created at the first question
      _saving = true;
    });
  }

  /// Reopens a saved chat: its messages, photos and the agent's memory
  /// (the backend rebuilds it from the `history` sent with each question).
  Future<void> _openConversation(ConversationSummary conversation) async {
    Navigator.pop(context); // close the history panel
    setState(() => _opening = true);
    try {
      final stored = await _history.loadMessages(conversation.id);
      if (!mounted) return;
      setState(() {
        _messages
          ..clear()
          ..addAll(stored.map(ChatMessage.stored));
        _conversationId = conversation.id;
        _saving = true;
      });
      _scrollToBottom();
    } catch (_) {
      _snack('Impossible de charger cette conversation.');
    } finally {
      if (mounted) setState(() => _opening = false);
    }
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(
          _scroll.position.maxScrollExtent,
          duration: const Duration(milliseconds: 250),
          curve: Curves.easeOut,
        );
      }
    });
  }

  void _snack(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  // ------------------------------------------------------------ UI

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      key: _scaffoldKey,
      appBar: AppBar(
        title: const Text('EMSI Chatbot'),
        backgroundColor: EmsiColors.green,
        foregroundColor: Colors.white,
        actions: [
          IconButton(
            tooltip: 'Nouvelle conversation',
            onPressed: _loading ? null : _newConversation,
            icon: const Icon(Icons.add_comment_outlined),
          ),
          IconButton(
            tooltip: 'Historique',
            onPressed: _loading
                ? null
                : () => _scaffoldKey.currentState!.openEndDrawer(),
            icon: const Icon(Icons.history),
          ),
        ],
      ),
      // The history panel, opened from the right.
      endDrawer: _HistoryDrawer(
        history: _history,
        currentId: _conversationId,
        onOpen: _openConversation,
        onNew: () {
          Navigator.pop(context);
          _newConversation();
        },
      ),
      body: Column(
        children: [
          _modeSelector(),
          Expanded(
            child: _opening
                ? const Center(
                    child: CircularProgressIndicator(color: EmsiColors.green),
                  )
                : _messages.isEmpty
                ? _emptyState()
                : ListView.builder(
                    controller: _scroll,
                    padding: const EdgeInsets.all(12),
                    itemCount: _messages.length + (_loading ? 1 : 0),
                    itemBuilder: (context, i) => i == _messages.length
                        ? const _TypingBubble()
                        : _MessageBubble(message: _messages[i]),
                  ),
          ),
          if (_photo != null) _photoPreview(),
          _inputBar(),
        ],
      ),
    );
  }

  Widget _modeSelector() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 0),
      child: SegmentedButton<String>(
        segments: const [
          ButtonSegment(
            value: 'classic',
            icon: Icon(Icons.bolt_outlined),
            label: Text('Classique'),
          ),
          ButtonSegment(
            value: 'agent',
            icon: Icon(Icons.smart_toy_outlined),
            label: Text('Agent'),
          ),
          ButtonSegment(
            value: 'multi',
            icon: Icon(Icons.groups_outlined),
            label: Text('Multi-agents'),
          ),
        ],
        selected: {_mode},
        onSelectionChanged: _loading
            ? null
            : (s) => setState(() => _mode = s.first),
      ),
    );
  }

  Widget _emptyState() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.forum_outlined, size: 64, color: EmsiColors.green),
            const SizedBox(height: 12),
            const Text(
              'Posez une question sur la brochure EMSI',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 8),
            Text(
              switch (_mode) {
                'classic' =>
                  'Mode classique : une recherche, une réponse (plus rapide).',
                'agent' => 'Mode agent : l’IA choisit ses outils et se souvient de la conversation.',
                _ =>
                  'Mode multi-agents : un superviseur répartit le travail entre '
                      'un agent Document et un agent Vision, puis un agent '
                      'Vérificateur contrôle la réponse (plus lent, plus fiable).',
              },
              textAlign: TextAlign.center,
              style: TextStyle(color: Colors.grey.shade600),
            ),
          ],
        ),
      ),
    );
  }

  Widget _photoPreview() {
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
      alignment: Alignment.centerLeft,
      child: Stack(
        children: [
          ClipRRect(
            borderRadius: BorderRadius.circular(10),
            child: Image.memory(_photo!, height: 80, fit: BoxFit.cover),
          ),
          Positioned(
            right: 0,
            top: 0,
            child: InkWell(
              onTap: () => setState(() => _photo = null),
              child: const CircleAvatar(
                radius: 12,
                backgroundColor: Colors.black54,
                child: Icon(Icons.close, size: 16, color: Colors.white),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _inputBar() {
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.all(8),
        child: Row(
          children: [
            IconButton(
              tooltip: 'Joindre une photo',
              onPressed: _loading ? null : _choosePhotoSource,
              icon: const Icon(
                Icons.add_photo_alternate_outlined,
                color: EmsiColors.green,
              ),
            ),
            Expanded(
              child: TextField(
                controller: _input,
                minLines: 1,
                maxLines: 4,
                textInputAction: TextInputAction.send,
                onSubmitted: (_) => _send(),
                decoration: InputDecoration(
                  hintText: 'Votre question…',
                  filled: true,
                  fillColor: Colors.grey.shade100,
                  contentPadding: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 12,
                  ),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(24),
                    borderSide: BorderSide.none,
                  ),
                ),
              ),
            ),
            const SizedBox(width: 6),
            IconButton.filled(
              tooltip: 'Envoyer',
              style: IconButton.styleFrom(backgroundColor: EmsiColors.green),
              onPressed: _loading ? null : _send,
              icon: _loading
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(
                        strokeWidth: 2,
                        color: Colors.white,
                      ),
                    )
                  : const Icon(Icons.send),
            ),
          ],
        ),
      ),
    );
  }
}

/// A user (right, green) or assistant (left, grey) bubble.
class _MessageBubble extends StatelessWidget {
  const _MessageBubble({required this.message});

  final ChatMessage message;

  @override
  Widget build(BuildContext context) {
    final fromUser = message.fromUser;
    final Color background = message.isError
        ? Colors.red.shade50
        : fromUser
        ? EmsiColors.green
        : Colors.grey.shade200;
    final Color textColor = message.isError
        ? Colors.red.shade800
        : fromUser
        ? Colors.white
        : Colors.black87;
    final answer = message.answer;

    return Align(
      alignment: fromUser ? Alignment.centerRight : Alignment.centerLeft,
      child: ConstrainedBox(
        constraints: BoxConstraints(
          maxWidth: min(MediaQuery.of(context).size.width * 0.8, 560),
        ),
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 5),
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: background,
            borderRadius: BorderRadius.only(
              topLeft: const Radius.circular(16),
              topRight: const Radius.circular(16),
              bottomLeft: Radius.circular(fromUser ? 16 : 4),
              bottomRight: Radius.circular(fromUser ? 4 : 16),
            ),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (message.photo != null)
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(10),
                    child: Image.memory(message.photo!, height: 160),
                  ),
                ),
              if (message.isError)
                Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Icon(Icons.error_outline, color: Colors.red.shade700),
                ),
              SelectableText(
                message.text,
                style: TextStyle(color: textColor, fontSize: 15, height: 1.35),
              ),
              if (answer != null && answer.imageUrls.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 10),
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(10),
                    child: Image.network(
                      answer.imageUrls.first,
                      height: 160,
                      errorBuilder: (_, _, _) =>
                          const Text('(image indisponible)'),
                    ),
                  ),
                ),
              if (answer != null && answer.sources.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 10),
                  child: Wrap(
                    spacing: 6,
                    runSpacing: 6,
                    children: [
                      for (final s in answer.sources)
                        Chip(
                          visualDensity: VisualDensity.compact,
                          materialTapTargetSize:
                              MaterialTapTargetSize.shrinkWrap,
                          avatar: const Icon(
                            Icons.description_outlined,
                            size: 16,
                            color: EmsiColors.green,
                          ),
                          label: Text('p. ${s.page}'),
                          backgroundColor: Colors.white,
                          side: BorderSide(color: Colors.grey.shade300),
                        ),
                    ],
                  ),
                ),
              if (answer != null && answer.agents.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(
                        Icons.groups_outlined,
                        size: 14,
                        color: EmsiColors.green,
                      ),
                      const SizedBox(width: 4),
                      // The path of the question through the agents.
                      Flexible(
                        child: Text(
                          answer.agents.join(' → '),
                          style: const TextStyle(
                            fontSize: 12,
                            color: EmsiColors.green,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              if (answer != null)
                Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: Text(
                    [
                      '${answer.seconds.toStringAsFixed(1)} s',
                      if (answer.toolCalls.isNotEmpty)
                        'outils : ${answer.toolCalls.toSet().join(', ')}',
                    ].join(' · '),
                    style: TextStyle(fontSize: 11, color: Colors.grey.shade600),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Side panel: the user's saved conversations (most recent first).
/// It is rebuilt each time it opens, so the list is always up to date.
class _HistoryDrawer extends StatefulWidget {
  const _HistoryDrawer({
    required this.history,
    required this.currentId,
    required this.onOpen,
    required this.onNew,
  });

  final ChatHistory history;
  final String? currentId; // the chat on screen (highlighted)
  final void Function(ConversationSummary) onOpen;
  final VoidCallback onNew;

  @override
  State<_HistoryDrawer> createState() => _HistoryDrawerState();
}

class _HistoryDrawerState extends State<_HistoryDrawer> {
  late final Future<List<ConversationSummary>> _conversations = widget.history
      .listConversations();

  /// "14:32" today, otherwise "24/11/2026".
  static String _date(DateTime d) {
    final now = DateTime.now();
    String two(int n) => n.toString().padLeft(2, '0');
    final today =
        d.year == now.year && d.month == now.month && d.day == now.day;
    return today
        ? '${two(d.hour)}:${two(d.minute)}'
        : '${two(d.day)}/${two(d.month)}/${d.year}';
  }

  @override
  Widget build(BuildContext context) {
    return Drawer(
      child: SafeArea(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Padding(
              padding: EdgeInsets.fromLTRB(16, 16, 16, 8),
              child: Text(
                'Historique',
                style: TextStyle(fontSize: 20, fontWeight: FontWeight.w700),
              ),
            ),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: FilledButton.icon(
                style: FilledButton.styleFrom(
                  backgroundColor: EmsiColors.green,
                ),
                onPressed: widget.onNew,
                icon: const Icon(Icons.add),
                label: const Text('Nouvelle conversation'),
              ),
            ),
            const SizedBox(height: 8),
            const Divider(height: 1),
            Expanded(
              child: FutureBuilder<List<ConversationSummary>>(
                future: _conversations,
                builder: (context, snapshot) {
                  if (snapshot.connectionState != ConnectionState.done) {
                    return const Center(
                      child: CircularProgressIndicator(color: EmsiColors.green),
                    );
                  }
                  if (snapshot.hasError) {
                    return const Center(
                      child: Text('Impossible de charger l’historique.'),
                    );
                  }
                  final items = snapshot.data!;
                  if (items.isEmpty) {
                    return Center(
                      child: Text(
                        'Aucune conversation enregistrée.',
                        style: TextStyle(color: Colors.grey.shade600),
                      ),
                    );
                  }
                  return ListView.builder(
                    itemCount: items.length,
                    itemBuilder: (context, i) {
                      final c = items[i];
                      return ListTile(
                        selected: c.id == widget.currentId,
                        selectedColor: EmsiColors.green,
                        leading: const Icon(Icons.chat_bubble_outline),
                        title: Text(
                          c.title,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                        ),
                        subtitle: Text(_date(c.updatedAt)),
                        onTap: () => widget.onOpen(c),
                      );
                    },
                  );
                },
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// "The assistant is thinking" bubble.
class _TypingBubble extends StatelessWidget {
  const _TypingBubble();

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 5),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
        decoration: BoxDecoration(
          color: Colors.grey.shade200,
          borderRadius: BorderRadius.circular(16),
        ),
        child: const Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            SizedBox(
              width: 16,
              height: 16,
              child: CircularProgressIndicator(
                strokeWidth: 2,
                color: EmsiColors.green,
              ),
            ),
            SizedBox(width: 10),
            Text('Recherche dans le document…'),
          ],
        ),
      ),
    );
  }
}
