import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/widgets.dart';
import 'package:matchuri/app.dart';
import 'package:matchuri/services/image_cache_manager.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  CachedNetworkImageProvider.defaultCacheManager = imageCacheManager;
  runApp(const MatchuriApp());
}
