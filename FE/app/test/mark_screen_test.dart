import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:matchuri/models/restaurant_card.dart';
import 'package:matchuri/models/store.dart';
import 'package:matchuri/screens/mark_screen.dart';
import 'package:matchuri/services/bookmark_api.dart';
import 'package:matchuri/services/restaurant_detail_api.dart';

void main() {
  testWidgets('filters bookmarked restaurants by category', (tester) async {
    tester.view.devicePixelRatio = 1;
    tester.view.physicalSize = const Size(320, 640);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.view.resetPhysicalSize);
    final semantics = tester.ensureSemantics();
    final api = _BookmarkApi([_card(1, '한식집', '한식'), _card(2, '초밥집', '일식')]);
    await tester.pumpWidget(
      MaterialApp(
        home: MarkScreen(
          api: api,
          detailApi: RestaurantDetailApi(
            userId: 'user-1',
            sessionId: 'session-1',
            client: MockClient((_) async => http.Response('{}', 200)),
          ),
          bookmarkVersion: ValueNotifier(0),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('한식집'), findsOneWidget);
    expect(find.text('초밥집'), findsNothing);
    expect(find.text('총 1건'), findsOneWidget);

    await tester.tap(find.text('일식'));
    await tester.pump();

    expect(find.text('한식집'), findsNothing);
    expect(find.text('초밥집'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('bookmark-2')));
    await tester.pumpAndSettle();
    expect(find.text('찜을 삭제할까요?'), findsOneWidget);
    expect(find.text('초밥집을(를) 찜 목록에서 삭제합니다.\n정말 삭제하시겠습니까?'), findsOneWidget);
    expect(tester.takeException(), isNull);

    await tester.tap(find.text('취소'));
    await tester.pumpAndSettle();
    expect(api.removedIds, isEmpty);
    semantics.dispose();
  });
}

RestaurantCard _card(int id, String name, String category) => RestaurantCard(
  cardId: id,
  image: null,
  store: Store(
    id: 'store-$id',
    name: name,
    location: '서울',
    category: category,
    categoryGroup: category,
    score: null,
  ),
);

class _BookmarkApi extends BookmarkApi {
  final List<RestaurantCard> cards;
  final removedIds = <int>[];

  _BookmarkApi(this.cards)
    : super(
        userId: 'user-1',
        client: MockClient((_) async => http.Response('[]', 200)),
      );

  @override
  Future<List<RestaurantCard>> fetchBookmarks({
    int limit = 50,
    int offset = 0,
  }) async => cards;

  @override
  Future<void> unbookmark(int cardId) async => removedIds.add(cardId);
}
