import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:matchuri/models/restaurant_card.dart';
import 'package:matchuri/models/store.dart';
import 'package:matchuri/services/bookmark_api.dart';
import 'package:matchuri/services/restaurant_detail_api.dart';
import 'package:matchuri/widgets/card_widget.dart';

void main() {
  testWidgets('removes an existing bookmark', (tester) async {
    final methods = <String>[];
    final bookmarkApi = BookmarkApi(
      userId: 'user-1',
      client: MockClient((request) async {
        methods.add(request.method);
        return http.Response('', request.method == 'DELETE' ? 204 : 200);
      }),
    );
    final detailApi = RestaurantDetailApi(
      userId: 'user-1',
      sessionId: 'session-1',
      client: MockClient((_) async => http.Response('{}', 200)),
    );
    addTearDown(bookmarkApi.dispose);
    addTearDown(detailApi.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: CardWidget(
            card: RestaurantCard(
              cardId: 1,
              image: null,
              menus: const [],
              store: Store(
                id: 'store-1',
                name: 'Restaurant',
                location: 'Seoul',
                category: 'Other',
                score: null,
              ),
            ),
            bookmarkApi: bookmarkApi,
            detailApi: detailApi,
            initialBookmarked: true,
          ),
        ),
      ),
    );
    await tester.tap(find.byType(CardWidget));
    await tester.pumpAndSettle();
    expect(find.byIcon(Icons.bookmark), findsOneWidget);
    await tester.tap(find.byIcon(Icons.bookmark));
    await tester.pumpAndSettle();
    expect(methods, ['DELETE']);
    expect(find.byIcon(Icons.bookmark_border), findsOneWidget);
  });
}
