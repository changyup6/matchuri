import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';

Future<void> showOriginalPhotoViewer(
  BuildContext context, {
  required Future<String> Function() loadUrl,
}) => Navigator.of(context).push<void>(
  MaterialPageRoute(
    fullscreenDialog: true,
    builder: (_) => OriginalPhotoViewer(loadUrl: loadUrl),
  ),
);

class OriginalPhotoViewer extends StatefulWidget {
  final Future<String> Function() loadUrl;

  const OriginalPhotoViewer({super.key, required this.loadUrl});

  @override
  State<OriginalPhotoViewer> createState() => _OriginalPhotoViewerState();
}

class _OriginalPhotoViewerState extends State<OriginalPhotoViewer> {
  late Future<String> _url;
  int _attempt = 0;

  @override
  void initState() {
    super.initState();
    _url = Future.sync(widget.loadUrl);
  }

  void _retry() {
    setState(() {
      _attempt++;
      _url = Future.sync(widget.loadUrl);
    });
  }

  Widget _error() => Center(
    child: Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        const Text('사진을 불러오지 못했어요.', style: TextStyle(color: Colors.white)),
        const SizedBox(height: 12),
        FilledButton(onPressed: _retry, child: const Text('다시 시도')),
      ],
    ),
  );

  @override
  Widget build(BuildContext context) => Scaffold(
    backgroundColor: Colors.black,
    appBar: AppBar(
      backgroundColor: Colors.black,
      foregroundColor: Colors.white,
      title: const Text('원본 사진'),
      leading: IconButton(
        tooltip: '닫기',
        onPressed: () => Navigator.of(context).pop(),
        icon: const Icon(Icons.close),
      ),
    ),
    body: FutureBuilder<String>(
      future: _url,
      builder: (context, snapshot) {
        if (snapshot.connectionState != ConnectionState.done) {
          return const Center(child: CircularProgressIndicator());
        }
        if (snapshot.hasError || !snapshot.hasData) return _error();
        return InteractiveViewer(
          key: ValueKey(_attempt),
          minScale: 1,
          maxScale: 5,
          child: SizedBox.expand(
            child: Image(
              // Fetch original bytes, but keep the decoded bitmap bounded and
              // preserve its aspect ratio, including very large source photos.
              image: ResizeImage(
                CachedNetworkImageProvider(snapshot.data!),
                width: 4096,
                height: 4096,
                policy: ResizeImagePolicy.fit,
                allowUpscaling: false,
              ),
              fit: BoxFit.contain,
              frameBuilder: (_, child, frame, loadedSynchronously) =>
                  loadedSynchronously || frame != null
                  ? child
                  : const Center(child: CircularProgressIndicator()),
              errorBuilder: (_, _, _) => _error(),
            ),
          ),
        );
      },
    ),
  );
}
