import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../widgets/user_avatar.dart';

class ProfilePage extends StatefulWidget {
  const ProfilePage({super.key});

  @override
  State<ProfilePage> createState() => _ProfilePageState();
}

class _ProfilePageState extends State<ProfilePage> {
  static const int _maxImageSize = 2 * 1024 * 1024;
  final ImagePicker _picker = ImagePicker();
  Uint8List? _previewBytes;
  bool _saving = false;
  late User _user;

  @override
  void initState() {
    super.initState();
    _user = Supabase.instance.client.auth.currentUser!;
  }

  String? get _avatarPath => _user.userMetadata?['avatar_path']?.toString();

  void _showMessage(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(message)));
  }

  String? _contentType(String fileName) {
    final extension = fileName.split('.').last.toLowerCase();
    return switch (extension) {
      'jpg' || 'jpeg' => 'image/jpeg',
      'png' => 'image/png',
      'webp' => 'image/webp',
      _ => null,
    };
  }

  Future<void> _selectAndUploadAvatar() async {
    if (_saving) return;

    try {
      final image = await _picker.pickImage(source: ImageSource.gallery);
      if (image == null) return;

      final contentType = _contentType(image.name);
      if (contentType == null) {
        _showMessage('Choisissez une image JPG, PNG ou WebP.');
        return;
      }

      final bytes = await image.readAsBytes();
      if (bytes.length > _maxImageSize) {
        _showMessage('La taille de l’image ne doit pas dépasser 2 Mo.');
        return;
      }

      setState(() {
        _previewBytes = bytes;
        _saving = true;
      });

      final client = Supabase.instance.client;
      final previousPath = _avatarPath;
      final path =
          '${_user.id}/${DateTime.now().microsecondsSinceEpoch}.${image.name.split('.').last.toLowerCase()}';

      await client.storage
          .from('avatars')
          .uploadBinary(
            path,
            bytes,
            fileOptions: FileOptions(contentType: contentType, upsert: false),
          );

      try {
        final metadata = Map<String, dynamic>.from(_user.userMetadata ?? {});
        metadata['avatar_path'] = path;
        await client.auth.updateUser(UserAttributes(data: metadata));
        _user = client.auth.currentUser!;
      } catch (error) {
        try {
          await client.storage.from('avatars').remove([path]);
        } catch (cleanupError) {
          debugPrint(
            'Impossible de supprimer l’avatar non enregistré : $cleanupError',
          );
        }
        rethrow;
      }

      var oldAvatarCleanupFailed = false;
      if (previousPath != null && previousPath != path) {
        try {
          await client.storage.from('avatars').remove([previousPath]);
        } catch (error) {
          oldAvatarCleanupFailed = true;
          debugPrint('Impossible de supprimer l’ancien avatar : $error');
        }
      }

      if (mounted) setState(() {});
      _showMessage(
        oldAvatarCleanupFailed
            ? 'Photo mise à jour, mais l’ancienne image n’a pas pu être supprimée.'
            : 'Photo de profil mise à jour.',
      );
    } on AuthException catch (error) {
      _showMessage('Enregistrement impossible : ${error.message}');
    } on StorageException catch (error) {
      _showMessage('Enregistrement impossible : ${error.message}');
    } catch (error) {
      debugPrint('Erreur lors du chargement de l’avatar : $error');
      _showMessage(
        'Impossible de charger cette image. Vérifiez le réseau et réessayez.',
      );
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final name = _user.userMetadata?['display_name']?.toString().trim();

    return Scaffold(
      appBar: AppBar(
        title: const Text('Mon profil'),
        backgroundColor: Colors.teal,
        foregroundColor: Colors.white,
      ),
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 480),
            child: Column(
              children: [
                const SizedBox(height: 20),
                Stack(
                  alignment: Alignment.bottomRight,
                  children: [
                    CircleAvatar(
                      radius: 55,
                      backgroundColor: Colors.teal.shade50,
                      child: ClipOval(
                        child: SizedBox.square(
                          dimension: 110,
                          child: _previewBytes != null
                              ? Image.memory(_previewBytes!, fit: BoxFit.cover)
                              : UserAvatar(
                                  path: _avatarPath,
                                  radius: 55,
                                  backgroundColor: Colors.teal.shade50,
                                ),
                        ),
                      ),
                    ),
                    Material(
                      color: Colors.teal,
                      shape: const CircleBorder(),
                      child: IconButton(
                        tooltip: 'Modifier la photo',
                        onPressed: _saving ? null : _selectAndUploadAvatar,
                        color: Colors.white,
                        icon: const Icon(Icons.camera_alt),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                TextButton.icon(
                  onPressed: _saving ? null : _selectAndUploadAvatar,
                  icon: const Icon(Icons.photo_library_outlined),
                  label: const Text('Changer la photo'),
                ),
                const SizedBox(height: 24),
                Card(
                  child: Padding(
                    padding: const EdgeInsets.all(20),
                    child: Column(
                      children: [
                        ListTile(
                          leading: const Icon(Icons.person_outline),
                          title: const Text('Nom'),
                          subtitle: Text(
                            name == null || name.isEmpty
                                ? 'Smart_App User'
                                : name,
                          ),
                        ),
                        const Divider(),
                        ListTile(
                          leading: const Icon(Icons.email_outlined),
                          title: const Text('Email'),
                          subtitle: Text(_user.email ?? 'Email indisponible'),
                        ),
                      ],
                    ),
                  ),
                ),
                if (_saving) ...[
                  const SizedBox(height: 24),
                  const CircularProgressIndicator(),
                  const SizedBox(height: 10),
                  const Text('Enregistrement de la photo…'),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}
