import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:matchuri/models/restaurant_card.dart';
import 'package:matchuri/services/bookmark_api.dart';
import 'package:matchuri/services/restaurant_detail_api.dart';
import 'package:matchuri/widgets/card_widget.dart';

RestaurantCard card(int id, {bool? bookmarked, bool menus = true}) =>
    RestaurantCard.fromJson({
      'card_id': id,
      'bookmarked': ?bookmarked,
      'distance_m': 750,
      'store': {
        'id': '$id',
        'name': '테스트 식당 $id',
        'location': '서울',
        'category': '일식',
        'lat': 37.5,
        'lon': 127.0,
      },
      if (menus) 'menus': <dynamic>[],
    });

RestaurantDetailApi detailApi() => RestaurantDetailApi(
  userId: 'test',
  sessionId: 'session-test',
  client: MockClient((_) async => http.Response('{}', 500)),
);

Future<void> mount(
  WidgetTester tester,
  RestaurantCard value,
  BookmarkApi api,
  RestaurantDetailApi detail, {
  bool initialBookmarked = false,
}) async {
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: SizedBox(
          width: 390,
          height: 650,
          child: CardWidget(
            card: value,
            bookmarkApi: api,
            detailApi: detail,
            initialBookmarked: initialBookmarked,
          ),
        ),
      ),
    ),
  );
  await tester.tap(find.text(value.store.name));
  await tester.pumpAndSettle();
}

void main() {
  test(
    'server bookmark is parsed while location and older fallback remain',
    () {
      expect(card(1, bookmarked: true).bookmarked, isTrue);
      expect(card(1, bookmarked: false).hasBookmarkState, isTrue);
      expect(card(1).bookmarked, isFalse);
      expect(card(1).hasBookmarkState, isFalse);
      expect(card(1).distanceMeters, 750);
      expect(card(1).latitude, 37.5);
      expect(card(1).longitude, 127.0);
    },
  );

  test(
    'failed mutations leave server state unchanged and do not notify',
    () async {
      final api = BookmarkApi(
        userId: 'test',
        client: MockClient((_) async => http.Response('', 500)),
      );
      addTearDown(api.dispose);
      var notifications = 0;
      api.addListener(() => notifications++);
      await expectLater(api.bookmark(1), throwsException);
      expect(api.isBookmarked(card(1)), isFalse);
      await expectLater(api.unbookmark(2), throwsException);
      expect(api.isBookmarked(card(2, bookmarked: true)), isTrue);
      expect(notifications, 0);
    },
  );

  test('same-user clients share changes and isolate other users', () async {
    final requests = <String>[];
    final first = BookmarkApi(
      userId: 'same-user',
      client: MockClient((request) async {
        requests.add('${request.method} ${request.url.path}');
        return http.Response('', 200);
      }),
    );
    final second = BookmarkApi(userId: 'same-user');
    final other = BookmarkApi(userId: 'different-user');
    addTearDown(first.dispose);
    addTearDown(second.dispose);
    addTearDown(other.dispose);
    var notices = 0;
    second.addListener(() => notices++);
    await first.bookmark(7);
    expect(second.isBookmarked(card(7)), isTrue);
    expect(other.isBookmarked(card(7)), isFalse);
    await first.unbookmark(7);
    expect(second.isBookmarked(card(7, bookmarked: true)), isFalse);
    expect(notices, 2);
    expect(requests, [
      'PUT /user/same-user/bookmarks/7',
      'DELETE /user/same-user/bookmarks/7',
    ]);
    first.dispose();
    second.dispose();
    final restarted = BookmarkApi(userId: 'same-user');
    addTearDown(restarted.dispose);
    expect(restarted.isBookmarked(card(7, bookmarked: true)), isTrue);
  });

  for (final firstValue in [true, false]) {
    test(
      'same-card ${firstValue ? 'PUT/DELETE' : 'DELETE/PUT'} writes stay ordered across clients',
      () async {
        final delayed = Completer<http.Response>();
        final requests = <String>[];
        var serverValue = !firstValue;
        Future<http.Response> handle(http.Request request) async {
          requests.add(request.method);
          serverValue = request.method == 'PUT';
          return requests.length == 1 ? delayed.future : http.Response('', 200);
        }

        final first = BookmarkApi(
          userId: 'ordered',
          client: MockClient(handle),
        );
        final second = BookmarkApi(
          userId: 'ordered',
          client: MockClient(handle),
        );
        addTearDown(first.dispose);
        addTearDown(second.dispose);
        final observed = <bool>[];
        second.addListener(() => observed.add(second.isBookmarked(card(7))));
        final early = firstValue ? first.bookmark(7) : first.unbookmark(7);
        await Future<void>.delayed(Duration.zero);
        final latest = firstValue ? second.unbookmark(7) : second.bookmark(7);
        await Future<void>.delayed(Duration.zero);

        // The first write reached the server but its response is delayed. The
        // later intent must wait, rather than racing either server or UI state.
        expect(requests, [firstValue ? 'PUT' : 'DELETE']);
        expect(serverValue, firstValue);
        delayed.complete(http.Response('', 200));
        await Future.wait([early, latest]);
        expect(requests, firstValue ? ['PUT', 'DELETE'] : ['DELETE', 'PUT']);
        expect(serverValue, !firstValue);
        expect(first.isBookmarked(card(7)), serverValue);
        expect(second.isBookmarked(card(7)), serverValue);
        expect(observed, [firstValue, !firstValue]);
      },
    );
  }

  for (final failFirst in [true, false]) {
    test(
      '${failFirst ? 'earlier' : 'later'} failed mutation preserves the last success and permits retries',
      () async {
        final delayed = Completer<http.Response>();
        var calls = 0;
        var serverValue = false;
        final api = BookmarkApi(
          userId: 'failure-order',
          client: MockClient((request) async {
            calls++;
            final response = calls == 1
                ? await delayed.future
                : http.Response('', calls == 2 && !failFirst ? 500 : 200);
            if (response.statusCode == 200) {
              serverValue = request.method == 'PUT';
            }
            return response;
          }),
        );
        addTearDown(api.dispose);
        final early = api.bookmark(7);
        final latest = api.unbookmark(7);
        final earlyChecked = failFirst
            ? expectLater(early, throwsException)
            : early;
        final latestChecked = failFirst
            ? latest
            : expectLater(latest, throwsException);
        delayed.complete(http.Response('', failFirst ? 500 : 200));
        await Future.wait([earlyChecked, latestChecked]);
        expect(calls, 2);
        expect(api.isBookmarked(card(7)), !failFirst);
        expect(serverValue, !failFirst);
        await api.unbookmark(7);
        expect(calls, 3);
        expect(serverValue, isFalse);
        expect(api.isBookmarked(card(7)), isFalse);
      },
    );
  }

  test('a pending write does not block different cards or users', () async {
    final delayed = Completer<http.Response>();
    final first = BookmarkApi(
      userId: 'independent',
      client: MockClient(
        (request) async => request.url.path.endsWith('/7')
            ? delayed.future
            : http.Response('', 200),
      ),
    );
    final other = BookmarkApi(
      userId: 'other-independent',
      client: MockClient((_) async => http.Response('', 200)),
    );
    addTearDown(first.dispose);
    addTearDown(other.dispose);
    final pending = first.bookmark(7);
    await Future.wait([first.bookmark(8), other.bookmark(7)]);
    expect(first.isBookmarked(card(7)), isFalse);
    expect(first.isBookmarked(card(8)), isTrue);
    expect(other.isBookmarked(card(7)), isTrue);
    delayed.complete(http.Response('', 200));
    await pending;
  });

  test(
    'disposing and reopening clients keeps pending server writes ordered',
    () async {
      final delayed = Completer<http.Response>();
      final requests = <String>[];
      var serverValue = false;
      final first = BookmarkApi(
        userId: 'reopened',
        client: MockClient((request) async {
          requests.add(request.method);
          serverValue = true;
          return delayed.future;
        }),
      );
      final early = first.bookmark(7);
      await Future<void>.delayed(Duration.zero);
      first.dispose();
      final reopened = BookmarkApi(
        userId: 'reopened',
        client: MockClient((request) async {
          requests.add(request.method);
          serverValue = false;
          return http.Response('', 200);
        }),
      );
      addTearDown(reopened.dispose);
      final observed = <bool>[];
      reopened.addListener(() => observed.add(reopened.isBookmarked(card(7))));
      final latest = reopened.unbookmark(7);
      await Future<void>.delayed(Duration.zero);
      expect(requests, ['PUT']);
      delayed.complete(http.Response('', 200));
      await Future.wait([early, latest]);
      expect(requests, ['PUT', 'DELETE']);
      expect(serverValue, isFalse);
      expect(reopened.isBookmarked(card(7)), isFalse);
      expect(observed, [true, false]);
    },
  );

  test(
    'disposing a queued client skips its request without blocking the next',
    () async {
      final delayed = Completer<http.Response>();
      final first = BookmarkApi(
        userId: 'queued-dispose',
        client: MockClient((_) => delayed.future),
      );
      var disposedCalls = 0;
      final closing = BookmarkApi(
        userId: 'queued-dispose',
        client: MockClient((_) async {
          disposedCalls++;
          return http.Response('', 200);
        }),
      );
      final last = BookmarkApi(
        userId: 'queued-dispose',
        client: MockClient((_) async => http.Response('', 200)),
      );
      addTearDown(first.dispose);
      addTearDown(closing.dispose);
      addTearDown(last.dispose);
      final early = first.bookmark(7);
      final cancelled = closing.unbookmark(7);
      final cancelledChecked = expectLater(cancelled, throwsStateError);
      final latest = last.unbookmark(7);
      closing.dispose();
      delayed.complete(http.Response('', 200));
      await Future.wait([early, cancelledChecked, latest]);
      expect(disposedCalls, 0);
      expect(first.isBookmarked(card(7)), isFalse);
      expect(last.isBookmarked(card(7)), isFalse);
    },
  );

  testWidgets('server true overrides incomplete list and can be removed', (
    tester,
  ) async {
    final methods = <String>[];
    final api = BookmarkApi(
      userId: 'test',
      client: MockClient((request) async {
        methods.add(request.method);
        return http.Response('', request.method == 'DELETE' ? 204 : 200);
      }),
    );
    final detail = detailApi();
    addTearDown(api.dispose);
    addTearDown(detail.dispose);
    await mount(tester, card(1, bookmarked: true), api, detail);
    expect(find.byIcon(Icons.bookmark), findsOneWidget);
    await tester.tap(find.byIcon(Icons.bookmark));
    await tester.pumpAndSettle();
    expect(methods, ['DELETE']);
    expect(find.byIcon(Icons.bookmark_border), findsOneWidget);
    ScaffoldMessenger.of(tester.element(find.byType(CardWidget)))
        .clearSnackBars();
    await tester.pumpAndSettle();
    await tester.tap(find.byIcon(Icons.bookmark_border));
    await tester.pumpAndSettle();
    expect(methods, ['DELETE', 'PUT']);
    expect(find.byIcon(Icons.bookmark), findsOneWidget);
  });

  testWidgets('server false overrides stale initial bookmarked hint', (
    tester,
  ) async {
    final api = BookmarkApi(userId: 'test');
    final detail = detailApi();
    addTearDown(api.dispose);
    addTearDown(detail.dispose);
    await mount(
      tester,
      card(1, bookmarked: false),
      api,
      detail,
      initialBookmarked: true,
    );
    expect(find.byIcon(Icons.bookmark_border), findsOneWidget);
  });

  testWidgets('another client removal updates a mounted saved card', (
    tester,
  ) async {
    final screenApi = BookmarkApi(userId: 'test');
    final otherScreenApi = BookmarkApi(
      userId: 'test',
      client: MockClient((_) async => http.Response('', 200)),
    );
    final detail = detailApi();
    addTearDown(screenApi.dispose);
    addTearDown(otherScreenApi.dispose);
    addTearDown(detail.dispose);
    await mount(tester, card(1, bookmarked: true), screenApi, detail);
    await otherScreenApi.unbookmark(1);
    await tester.pump();
    expect(find.byIcon(Icons.bookmark_border), findsOneWidget);
    await otherScreenApi.bookmark(1);
    await tester.pump();
    expect(find.byIcon(Icons.bookmark), findsOneWidget);
  });

  testWidgets('late bookmark success does not select the next reused card', (
    tester,
  ) async {
    final pending = Completer<http.Response>();
    final api = BookmarkApi(
      userId: 'test',
      client: MockClient((_) => pending.future),
    );
    final detail = detailApi();
    addTearDown(api.dispose);
    addTearDown(detail.dispose);
    await mount(tester, card(1), api, detail);
    await tester.tap(find.byIcon(Icons.bookmark_border));
    await tester.pump();
    await mount(tester, card(2), api, detail);
    pending.complete(http.Response('', 200));
    await tester.pumpAndSettle();
    expect(find.byIcon(Icons.bookmark_border), findsOneWidget);
    expect(api.isBookmarked(card(1)), isTrue);
    expect(api.isBookmarked(card(2)), isFalse);
    expect(find.text('찜 목록에 담았어요.'), findsNothing);
  });

  testWidgets('card or detail API replacement reloads lazy menus', (
    tester,
  ) async {
    final api = BookmarkApi(userId: 'test');
    final calls = <String>[];
    RestaurantDetailApi makeDetail(String name) => RestaurantDetailApi(
      userId: 'test',
      sessionId: name,
      client: MockClient((request) async {
        calls.add(request.url.path);
        return http.Response('{}', 500);
      }),
    );
    final first = makeDetail('first');
    final second = makeDetail('second');
    addTearDown(api.dispose);
    addTearDown(first.dispose);
    addTearDown(second.dispose);
    await mount(tester, card(1, menus: false), api, first);
    await mount(tester, card(2, menus: false), api, first);
    await mount(tester, card(2, menus: false), api, second);
    expect(calls, [
      '/user/test/session/first/cards/1',
      '/user/test/session/first/cards/2',
      '/user/test/session/second/cards/2',
    ]);
  });
}
