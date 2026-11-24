import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

class UserAvatar extends StatefulWidget {
  const UserAvatar({
    required this.path,
    required this.radius,
    this.backgroundColor = Colors.white,
    this.iconColor = Colors.teal,
    super.key,
  });

  final String? path;
  final double radius;
  final Color backgroundColor;
  final Color iconColor;

  @override
  State<UserAvatar> createState() => _UserAvatarState();
}

class _UserAvatarState extends State<UserAvatar> {
  late Future<String>? _signedUrl;

  @override
  void initState() {
    super.initState();
    _signedUrl = _createSignedUrl(widget.path);
  }

  @override
  void didUpdateWidget(covariant UserAvatar oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.path != widget.path) {
      _signedUrl = _createSignedUrl(widget.path);
    }
  }

  Future<String>? _createSignedUrl(String? path) {
    if (path == null || path.isEmpty) return null;
    return Supabase.instance.client.storage
        .from('avatars')
        .createSignedUrl(path, 60 * 60);
  }

  Widget _placeholder() =>
      Icon(Icons.person, size: widget.radius * 1.25, color: widget.iconColor);

  @override
  Widget build(BuildContext context) {
    return CircleAvatar(
      radius: widget.radius,
      backgroundColor: widget.backgroundColor,
      child: _signedUrl == null
          ? _placeholder()
          : FutureBuilder<String>(
              future: _signedUrl,
              builder: (context, snapshot) {
                if (!snapshot.hasData) return _placeholder();
                return ClipOval(
                  child: SizedBox.square(
                    dimension: widget.radius * 2,
                    child: Image.network(
                      snapshot.data!,
                      fit: BoxFit.cover,
                      errorBuilder: (context, error, stackTrace) =>
                          _placeholder(),
                    ),
                  ),
                );
              },
            ),
    );
  }
}
