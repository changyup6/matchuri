import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:cached_network_image/cached_network_image.dart';
import 'package:crypto/crypto.dart';
import 'package:file/file.dart' as cache_files;
import 'package:file/memory.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_cache_manager/flutter_cache_manager.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:matchuri/models/menu_item.dart';
import 'package:matchuri/models/restaurant_card.dart';
import 'package:matchuri/models/review.dart';
import 'package:matchuri/models/store.dart';
import 'package:matchuri/screens/restaurant_detail_screen.dart';
import 'package:matchuri/services/original_card_images.dart';
import 'package:matchuri/services/restaurant_detail_api.dart';
import 'package:matchuri/services/bookmark_api.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:matchuri/widgets/card_widget.dart';
import 'package:matchuri/widgets/menu_tile.dart';
import 'package:matchuri/widgets/original_photo_viewer.dart';

const hero = 'https://fixture.example/hero.jpg';
String menuUrl(int id) => 'https://fixture.example/menu-$id.jpg';
String thumbnail(String original) =>
    'https://api.example/media/catalog/v2/swipe/ab/${sha256.convert(utf8.encode(original))}.jpg';

RestaurantCard card({
  List<MenuItem>? menus,
  List<Review> reviews = const [],
  String? image = hero,
  List<String> images = const [hero],
}) => RestaurantCard(
  cardId: 42,
  store: Store(
    id: 'store-42',
    name: '테스트 식당',
    location: '테스트 주소',
    category: '한식',
    score: 4.5,
    reviews: reviews,
  ),
  image: image,
  images: images,
  menus: menus,
);

MenuItem menu(int id, {String? image}) => MenuItem(
  id: id,
  storeId: 'store-42',
  name: '메뉴 ${id + 1}',
  price: 12000,
  image: image ?? menuUrl(id),
);

class FakeApi extends RestaurantDetailApi {
  final RestaurantCard detail;
  int normalCalls = 0;
  int originalCalls = 0;
  int originalReviewCalls = 0;
  List<Review>? fullReviews;
  Future<RestaurantCard> Function()? originalResponse;

  FakeApi(this.detail) : super(userId: 'fixture', sessionId: 'fixture-session');

  @override
  Future<List<Review>> fetchReviews(
    int cardId, {
    int limit = 0,
    bool originalImages = false,
  }) async {
    if (originalImages) originalReviewCalls++;
    return fullReviews ?? detail.store.reviews;
  }

  @override
  Future<RestaurantCard> fetchCardDetail(
    int cardId, {
    bool originalImages = false,
  }) async {
    if (originalImages) {
      originalCalls++;
      return originalResponse != null ? originalResponse!() : detail;
    }
    normalCalls++;
    return detail;
  }
}

BookmarkApi bookmarks() {
  final api = BookmarkApi(
    userId: 'fixture',
    client: MockClient((_) async => http.Response('[]', 200)),
  );
  addTearDown(api.dispose);
  return api;
}

class RecordingImageCache extends Fake implements BaseCacheManager {
  final cache_files.File image;
  final List<String> requested = [];
  RecordingImageCache(List<int> bytes)
    : image = MemoryFileSystem().file('/fixture.jpg')..writeAsBytesSync(bytes);

  @override
  Stream<FileResponse> getFileStream(
    String url, {
    String? key,
    Map<String, String>? headers,
    bool withProgress = false,
  }) async* {
    requested.add(url);
    yield FileInfo(image, FileSource.Cache, DateTime(2100), url);
  }
}

final captureKey = GlobalKey();

Widget screen(Widget child) => RepaintBoundary(
  key: captureKey,
  child: MaterialApp(
    debugShowCheckedModeBanner: false,
    theme: ThemeData(fontFamily: 'Paperlogy'),
    builder: (context, body) => Column(
      children: [
        const ColoredBox(
          color: Color(0xffFFF0D7),
          child: SizedBox(
            width: double.infinity,
            height: 32,
            child: Center(
              child: Text(
                '실제 Flutter 위젯 렌더링 · 테스트 데이터',
                style: TextStyle(
                  color: Colors.black,
                  fontFamily: 'Paperlogy',
                  fontSize: 10,
                  decoration: TextDecoration.none,
                ),
              ),
            ),
          ),
        ),
        Expanded(child: body!),
      ],
    ),
    home: child,
  ),
);

Future<void> capture(WidgetTester tester, String name) async {
  final directory = Platform.environment['MATCHURI_WIDGET_CAPTURE_DIR'];
  if (directory == null) return;
  await tester.pump();
  final boundary =
      captureKey.currentContext!.findRenderObject()! as RenderRepaintBoundary;
  await tester.runAsync(() async {
    final image = await boundary.toImage(pixelRatio: 2);
    final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
    await Directory(directory).create(recursive: true);
    await File('$directory/$name.png')
        .writeAsBytes(bytes!.buffer.asUint8List());
    image.dispose();
  });
}

Future<void> settle(WidgetTester tester) async {
  // Local fixture reads/engine image decoding run outside Flutter's fake clock.
  for (var i = 0; i < 4; i++) {
    await tester.pump(const Duration(milliseconds: 50));
    await tester.runAsync(
      () => Future<void>.delayed(const Duration(milliseconds: 35)),
    );
  }
  await tester.pumpAndSettle(
    const Duration(milliseconds: 50),
    EnginePhase.sendSemanticsUpdate,
    const Duration(seconds: 3),
  );
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  late Directory temporary;
  late BaseCacheManager previous;
  late RecordingImageCache cache;

  setUpAll(() async {
    if (Platform.environment['MATCHURI_WIDGET_CAPTURE_DIR'] != null) {
      final fonts = FontLoader('Paperlogy')
        ..addFont(rootBundle.load('assets/fonts/Paperlogy-4Regular.ttf'))
        ..addFont(rootBundle.load('assets/fonts/Paperlogy-7Bold.ttf'));
      await fonts.load();
      final icons = FontLoader('MaterialIcons')
        ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf'));
      await icons.load();
    }
    temporary = await Directory.systemTemp.createTemp('matchuri-photo-tests-');
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
          const MethodChannel('plugins.flutter.io/path_provider'),
          (_) async => temporary.path,
        );
    final fixture = Platform.environment['MATCHURI_WIDGET_PHOTO'];
    final image = File('${temporary.path}/photo.png');
    await image.writeAsBytes(
      fixture != null
          ? await File(fixture).readAsBytes()
          : base64Decode(
              'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
            ),
    );
    cache = RecordingImageCache(await image.readAsBytes());
    previous = CachedNetworkImageProvider.defaultCacheManager;
    CachedNetworkImageProvider.defaultCacheManager = cache;
  });
  tearDownAll(() async {
    CachedNetworkImageProvider.defaultCacheManager = previous;
    await previous.dispose();
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
          const MethodChannel('plugins.flutter.io/path_provider'),
          null,
        );
    await temporary.delete(recursive: true);
  });
  setUp(() {
    cache.requested.clear();
    PaintingBinding.instance.imageCache.clear();
    PaintingBinding.instance.imageCache.clearLiveImages();
  });

  testWidgets(
    'collapsed card has no detail call; expansion loads once and only visible menu images',
    (tester) async {
      await tester.binding.setSurfaceSize(const Size(390, 844));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      final detail = card(menus: List.generate(40, (index) => menu(index)));
      final api = FakeApi(detail);
      addTearDown(api.dispose);
      await tester.pumpWidget(
        screen(
          Scaffold(
            body: Padding(
              padding: const EdgeInsets.all(16),
              child: CardWidget(
                card: card(),
                detailApi: api,
                bookmarkApi: bookmarks(),
              ),
            ),
          ),
        ),
      );
      await settle(tester);
      expect(api.normalCalls, 0);
      expect(api.originalCalls, 0);
      expect(cache.requested.where((url) => url.contains('/menu-')), isEmpty);
      await capture(tester, 'collapsed-card');

      await tester.tap(find.text('테스트 식당'));
      await settle(tester);
      expect(api.normalCalls, 1);
      expect(api.originalCalls, 0);
      expect(find.byType(MenuTile).evaluate().length, lessThan(40));
      expect(
        cache.requested.where((url) => url.contains('/menu-')).toSet().length,
        lessThan(40),
      );
      expect(cache.requested, isNot(contains(menuUrl(39))));
      await capture(tester, 'expanded-card');

      await tester.tapAt(const Offset(150, 110));
      await settle(tester);
      await tester.tap(find.text('테스트 식당'));
      await settle(tester);
      expect(api.normalCalls, 1);

      await tester.tap(find.byType(MenuTile).first);
      await settle(tester);
      expect(api.originalCalls, 1);
      expect(find.text('원본 사진'), findsOneWidget);
      expect(find.byType(InteractiveViewer), findsOneWidget);
      await tester.tap(find.byTooltip('닫기'));
      await settle(tester);
      expect(find.text('원본 사진'), findsNothing);
    },
  );

  testWidgets(
    'detail requests originals only on tap and reuses them across viewers',
    (tester) async {
      await tester.binding.setSurfaceSize(const Size(390, 844));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      final original = card(menus: [menu(0)]);
      final api = FakeApi(original);
      addTearDown(api.dispose);
      final shown = card(
        image: thumbnail(hero),
        images: [thumbnail(hero)],
        menus: [menu(0, image: thumbnail(menuUrl(0)))],
      );
      await tester.pumpWidget(
        screen(
          RestaurantDetailScreen(
            card: shown,
            api: api,
            bookmarkApi: bookmarks(),
          ),
        ),
      );
      await settle(tester);
      expect(api.normalCalls, 0);
      expect(api.originalCalls, 0);
      expect(cache.requested, isNot(contains(hero)));
      await capture(tester, 'detail-screen');

      await tester.tap(find.byIcon(Icons.zoom_in));
      await settle(tester);
      expect(api.originalCalls, 1);
      expect(cache.requested, contains(hero));
      final image = tester.widget<Image>(
        find.descendant(
          of: find.byType(InteractiveViewer),
          matching: find.byType(Image),
        ),
      );
      final provider = image.image as ResizeImage;
      expect(provider.width, 4096);
      expect(provider.height, 4096);
      expect(provider.policy, ResizeImagePolicy.fit);
      expect(provider.allowUpscaling, isFalse);
      await capture(tester, 'original-viewer');
      await tester.tap(find.byTooltip('닫기'));
      await settle(tester);
      await tester.tap(find.byIcon(Icons.zoom_in));
      await settle(tester);
      expect(api.originalCalls, 1);
    },
  );

  test(
    'review originals beyond preview load on demand and share the full list',
    () async {
      const outsidePreview = 'https://fixture.example/review-99.jpg';
      const anotherReview = 'https://fixture.example/review-100.jpg';
      final api = FakeApi(card(menus: []));
      addTearDown(api.dispose);
      api.fullReviews = [
        Review(
          id: 99,
          storeId: 'store-42',
          content: '',
          score: null,
          images: [outsidePreview],
        ),
        Review(
          id: 100,
          storeId: 'store-42',
          content: '',
          score: null,
          images: [anotherReview],
        ),
      ];
      final images = OriginalCardImages(api: api, cardId: 42);
      expect(api.originalCalls, 0);
      expect(api.originalReviewCalls, 0);
      expect(
        await images.review(99, 0, thumbnail(outsidePreview)),
        outsidePreview,
      );
      expect(api.originalCalls, 1);
      expect(api.originalReviewCalls, 1);
      expect(
        await images.review(100, 0, thumbnail(anotherReview)),
        anotherReview,
      );
      expect(api.originalReviewCalls, 1);
      await expectLater(
        images.review(99, 0, thumbnail(anotherReview)),
        throwsStateError,
      );
    },
  );

  testWidgets('original lookup errors offer retry and close', (tester) async {
    final api = FakeApi(card(menus: []));
    addTearDown(api.dispose);
    var attempts = 0;
    api.originalResponse = () async {
      if (++attempts == 1) throw StateError('temporary API failure');
      return api.detail;
    };
    final images = OriginalCardImages(api: api, cardId: 42);
    await tester.pumpWidget(
      screen(
        Scaffold(
          body: Builder(
            builder: (context) => TextButton(
              onPressed: () => showOriginalPhotoViewer(
                context,
                loadUrl: () => images.hero(thumbnail(hero)),
              ),
              child: const Text('열기'),
            ),
          ),
        ),
      ),
    );
    expect(api.originalCalls, 0);
    await tester.tap(find.text('열기'));
    await settle(tester);
    expect(find.text('다시 시도'), findsOneWidget);
    expect(api.originalCalls, 1);
    await tester.tap(find.text('다시 시도'));
    await settle(tester);
    expect(api.originalCalls, 2);
    expect(find.byType(InteractiveViewer), findsOneWidget);
    await tester.tap(find.byTooltip('닫기'));
    await settle(tester);
    expect(find.text('열기'), findsOneWidget);
  });

  testWidgets('original request loading can be closed before completion', (
    tester,
  ) async {
    final pending = Completer<String>();
    var calls = 0;
    await tester.pumpWidget(
      screen(
        Scaffold(
          body: Builder(
            builder: (context) => TextButton(
              onPressed: () => showOriginalPhotoViewer(
                context,
                loadUrl: () {
                  calls++;
                  return pending.future;
                },
              ),
              child: const Text('열기'),
            ),
          ),
        ),
      ),
    );
    expect(calls, 0);
    await tester.tap(find.text('열기'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    expect(calls, 1);
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    await tester.tap(find.byTooltip('닫기'));
    await tester.pumpAndSettle();
    expect(find.text('원본 사진'), findsNothing);
    pending.complete(hero);
    await tester.pump();
    expect(tester.takeException(), isNull);
  });

  test('original selectors use IDs and reject reordered, missing or changed photos', () async {
    const reviewA = 'https://fixture.example/review-a.jpg';
    const reviewB = 'https://fixture.example/review-b.jpg';
    final api = FakeApi(
      card(
        menus: [menu(9), menu(4)],
        reviews: [
          Review(
            id: 7,
            storeId: 'store-42',
            content: '',
            score: null,
            images: [reviewA],
          ),
          Review(
            id: 2,
            storeId: 'store-42',
            content: '',
            score: null,
            images: [reviewB],
          ),
        ],
      ),
    );
    addTearDown(api.dispose);
    final images = OriginalCardImages(api: api, cardId: 42);
    expect(api.originalCalls, 0);
    expect(await images.menu(4, thumbnail(menuUrl(4))), menuUrl(4));
    expect(await images.review(2, 0, thumbnail(reviewB)), reviewB);
    expect(await images.gallery(0, thumbnail(hero)), hero);
    expect(api.originalCalls, 1);
    await expectLater(images.gallery(1, thumbnail(hero)), throwsStateError);
    await expectLater(images.gallery(0, thumbnail(reviewA)), throwsStateError);
    await expectLater(
      images.menu(999, thumbnail(menuUrl(4))),
      throwsStateError,
    );
    await expectLater(
      images.review(2, 1, thumbnail(reviewB)),
      throwsStateError,
    );
    await expectLater(
      images.review(8, 0, thumbnail(reviewB)),
      throwsStateError,
    );
    expect(await images.hero(hero), hero);
    expect(api.originalCalls, greaterThan(1));
  });
}
