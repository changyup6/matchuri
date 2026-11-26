import 'dart:convert';
import 'dart:io';

import 'package:cached_network_image/cached_network_image.dart';
import 'package:file/file.dart' as files;
import 'package:file/memory.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_cache_manager/flutter_cache_manager.dart' as cache;
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/io_client.dart';
import 'package:http/testing.dart';
import 'package:matchuri/models/restaurant_card.dart';
import 'package:matchuri/models/store.dart';
import 'package:matchuri/screens/home_screen.dart';
import 'package:matchuri/services/bookmark_api.dart';
import 'package:matchuri/services/preferences_api.dart';
import 'package:matchuri/services/restaurant_api.dart';
import 'package:matchuri/services/restaurant_detail_api.dart';

class _LocalHttp extends HttpOverrides {
  HttpClient client() => super.createHttpClient(null);
}

class _MemoryCacheFiles implements cache.FileSystem {
  final files.FileSystem filesystem = MemoryFileSystem();

  @override
  Future<files.File> createFile(String name) async => filesystem.file('/$name');
}

class _CardsApi extends RestaurantApi {
  final List<RestaurantCard> cards;

  _CardsApi(this.cards) : super(userId: 'prefetch', sessionId: 'session');

  @override
  Future<List<RestaurantCard>> fetchCards({int limit = 50}) async => cards;

  @override
  Future<double> fetchDecisionReadinessScore() async => 0;
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  late Directory temporary;
  late cache.BaseCacheManager previousCache;

  setUpAll(() async {
    temporary = await Directory.systemTemp.createTemp(
      'matchuri-prefetch-test-',
    );
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
          const MethodChannel('plugins.flutter.io/path_provider'),
          (_) async => temporary.path,
        );
    // Reading this field initializes the package's default disk cache. Keep
    // that initialization and disposal outside the widget test's fake clock.
    previousCache = CachedNetworkImageProvider.defaultCacheManager;
  });
  tearDownAll(() async {
    CachedNetworkImageProvider.defaultCacheManager = previousCache;
    await previousCache.dispose();
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
          const MethodChannel('plugins.flutter.io/path_provider'),
          null,
        );
    await temporary.delete(recursive: true);
  });

  testWidgets('home prefetch and visible cards share one HTTP image download', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(780, 1688);
    tester.view.devicePixelRatio = 2;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final imageBytes = base64Decode(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
    );
    final requests = <String, int>{};
    final server = (await tester.runAsync(
      () => HttpServer.bind(InternetAddress.loopbackIPv4, 0),
    ))!;
    server.listen((request) async {
      final path = request.uri.path;
      requests.update(path, (count) => count + 1, ifAbsent: () => 1);
      request.response.headers.contentType = ContentType('image', 'png');
      request.response.headers.set(
        HttpHeaders.cacheControlHeader,
        'max-age=3600',
      );
      request.response.add(imageBytes);
      await request.response.close();
    });

    // Keep both image transports real and count the requests at the local
    // server. Using different provider types must not silently fetch twice.
    final client = _LocalHttp().client();
    final previousNetworkClient = debugNetworkImageHttpClientProvider;
    debugNetworkImageHttpClientProvider = () => client;
    final imageCache = cache.CacheManager(
      cache.Config(
        'home-prefetch',
        repo: cache.NonStoringObjectProvider(),
        fileSystem: _MemoryCacheFiles(),
        fileService: cache.HttpFileService(httpClient: IOClient(client)),
      ),
    );
    CachedNetworkImageProvider.defaultCacheManager = imageCache;
    PaintingBinding.instance.imageCache.clear();
    PaintingBinding.instance.imageCache.clearLiveImages();

    final cards = [
      for (var id = 1; id <= 2; id++)
        RestaurantCard(
          cardId: id,
          store: Store(
            id: '$id',
            name: '식당 $id',
            location: '테스트 주소',
            category: '한식',
            score: 4.5,
          ),
          image: 'http://127.0.0.1:${server.port}/card-$id.png',
        ),
    ];
    final api = _CardsApi(cards);
    final bookmarks = BookmarkApi(
      userId: 'prefetch',
      client: MockClient((_) async => http.Response('[]', 200)),
    );
    final detail = RestaurantDetailApi(
      userId: 'prefetch',
      sessionId: 'session',
    );
    final preferences = PreferencesApi();

    try {
      await tester.pumpWidget(
        MaterialApp(
          home: HomeScreen(
            api: api,
            bookmarkApi: bookmarks,
            detailApi: detail,
            userId: 'prefetch',
            preferencesApi: preferences,
            onStartNewSession: () async {},
          ),
        ),
      );
      // Socket IO and engine image decoding run outside the fake test clock.
      for (var attempt = 0; attempt < 40; attempt++) {
        await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 25)),
        );
        await tester.pump(const Duration(milliseconds: 50));
        final rendered = tester
            .widgetList<RawImage>(find.byType(RawImage))
            .where((image) => image.image != null);
        if (rendered.length == 2) break;
      }
      await tester.pumpAndSettle();

      expect(
        tester
            .widgetList<RawImage>(find.byType(RawImage))
            .where((image) => image.image != null),
        hasLength(2),
      );
      expect(requests, {'/card-1.png': 1, '/card-2.png': 1});
      expect(tester.takeException(), isNull);
    } finally {
      await tester.pumpWidget(const SizedBox.shrink());
      // CacheManager schedules maintenance after reads but does not cancel
      // that timer on dispose. Let it finish while its repository is open.
      await tester.pump(const Duration(seconds: 10));
      debugNetworkImageHttpClientProvider = previousNetworkClient;
      CachedNetworkImageProvider.defaultCacheManager = previousCache;
      PaintingBinding.instance.imageCache.clear();
      PaintingBinding.instance.imageCache.clearLiveImages();
      await imageCache.dispose();
      api.dispose();
      bookmarks.dispose();
      detail.dispose();
      preferences.dispose();
      client.close(force: true);
      await tester.runAsync(() => server.close(force: true));
    }
  });
}
