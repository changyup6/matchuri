import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:matchuri/models/restaurant_card.dart';
import 'package:matchuri/models/store.dart';
import 'package:matchuri/screens/restaurant_detail_screen.dart';
import 'package:matchuri/services/bookmark_api.dart';
import 'package:matchuri/services/restaurant_detail_api.dart';

RestaurantCard card({bool? bookmarked, bool detailed = true}) => RestaurantCard(
  cardId: 99,
  bookmarked: bookmarked,
  image: null,
  menus: detailed ? const [] : null,
  store: Store(
    id: 'store-99',
    name: '식당',
    location: '서울',
    category: '한식',
    score: null,
  ),
);

Future<void> mount(
  WidgetTester tester,
  RestaurantCard value,
  BookmarkApi bookmarks,
  RestaurantDetailApi detail,
) => tester.pumpWidget(
  MaterialApp(
    home: RestaurantDetailScreen(
      card: value,
      api: detail,
      bookmarkApi: bookmarks,
    ),
  ),
);

void main() {
  testWidgets('toggles the detail bookmark icon and real API state', (
    tester,
  ) async {
    final methods = <String>[];
    final bookmarks = BookmarkApi(
      userId: 'test',
      client: MockClient((request) async {
        methods.add(request.method);
        return http.Response('', request.method == 'DELETE' ? 204 : 200);
      }),
    );
    final detail = RestaurantDetailApi(
      userId: 'test',
      sessionId: 'session',
      client: MockClient((_) async => http.Response('{}', 500)),
    );
    addTearDown(bookmarks.dispose);
    addTearDown(detail.dispose);
    await mount(tester, card(bookmarked: false), bookmarks, detail);
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('bookmark-icon-unselected')),
      findsOneWidget,
    );
    await tester.tap(find.byKey(const ValueKey('detail-bookmark-button')));
    await tester.pumpAndSettle();
    expect(bookmarks.isBookmarked(card()), isTrue);
    expect(
      find.byKey(const ValueKey('bookmark-icon-selected')),
      findsOneWidget,
    );
    await tester.tap(find.byKey(const ValueKey('detail-bookmark-button')));
    await tester.pumpAndSettle();
    expect(bookmarks.isBookmarked(card()), isFalse);
    expect(
      find.byKey(const ValueKey('bookmark-icon-unselected')),
      findsOneWidget,
    );
    expect(methods, ['PUT', 'DELETE']);
  });

  testWidgets(
    'server bookmarked detail beyond first list page needs no bookmark list',
    (tester) async {
      var bookmarkGets = 0;
      final bookmarks = BookmarkApi(
        userId: 'test',
        client: MockClient((request) async {
          if (request.method == 'GET') bookmarkGets++;
          return http.Response('[]', 200);
        }),
      );
      final detail = RestaurantDetailApi(
        userId: 'test',
        sessionId: 'session',
        client: MockClient(
          (_) async => http.Response(
            jsonEncode({
              'card_id': 99,
              'bookmarked': true,
              'menus': [],
              'store': {
                'id': 'store-99',
                'name': '식당',
                'location': '서울',
                'category': '한식',
              },
            }),
            200,
            headers: {'content-type': 'application/json; charset=utf-8'},
          ),
        ),
      );
      addTearDown(bookmarks.dispose);
      addTearDown(detail.dispose);
      await mount(tester, card(detailed: false), bookmarks, detail);
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('bookmark-icon-selected')),
        findsOneWidget,
      );
      expect(bookmarkGets, 0);
    },
  );

  testWidgets('other screen mutation wins over delayed detail snapshot', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    final bookmarks = BookmarkApi(userId: 'test');
    final other = BookmarkApi(
      userId: 'test',
      client: MockClient((_) async => http.Response('', 204)),
    );
    final detail = RestaurantDetailApi(
      userId: 'test',
      sessionId: 'session',
      client: MockClient((_) => pending.future),
    );
    addTearDown(bookmarks.dispose);
    addTearDown(other.dispose);
    addTearDown(detail.dispose);
    await mount(
      tester,
      card(bookmarked: true, detailed: false),
      bookmarks,
      detail,
    );
    await other.unbookmark(99);
    pending.complete(
      http.Response(
        jsonEncode({
          'card_id': 99,
          'bookmarked': true,
          'menus': [],
          'store': {
            'id': 'store-99',
            'name': '식당',
            'location': '서울',
            'category': '한식',
          },
        }),
        200,
        headers: {'content-type': 'application/json; charset=utf-8'},
      ),
    );
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('bookmark-icon-unselected')),
      findsOneWidget,
    );
  });
}
