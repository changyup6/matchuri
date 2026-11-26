import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:matchuri/services/bookmark_api.dart';
import 'package:matchuri/services/restaurant_api.dart';
import 'package:matchuri/services/restaurant_detail_api.dart';

void main() {
  test('loads every review with limit zero', () async {
    late http.Request request;
    final api = RestaurantDetailApi(
      userId: 'user-1',
      sessionId: 'session-1',
      client: MockClient((value) async {
        request = value;
        return http.Response(jsonEncode([]), 200);
      }),
    );
    await api.fetchReviews(42);
    expect(request.url.path, '/cards/42/reviews');
    expect(request.url.queryParameters['limit'], '0');
  });

  test(
    'scoped detail and full reviews request originals only when asked',
    () async {
      final requests = <http.Request>[];
      final api = RestaurantDetailApi(
        userId: 'user-1',
        sessionId: 'session-1',
        client: MockClient((request) async {
          requests.add(request);
          return http.Response(
            jsonEncode(
              request.url.path.endsWith('/reviews')
                  ? []
                  : {
                      'card_id': 42,
                      'store': {
                        'id': 'store-42',
                        'name': 'Restaurant',
                        'location': 'Seoul',
                        'category': 'Other',
                      },
                      'image': 'https://fixture.example/image.jpg',
                    },
            ),
            200,
          );
        }),
      );
      addTearDown(api.dispose);
      await api.fetchCardDetail(42);
      await api.fetchCardDetail(42, originalImages: true);
      await api.fetchReviews(42);
      await api.fetchReviews(42, originalImages: true);
      expect(requests[0].url.path, '/user/user-1/session/session-1/cards/42');
      expect(requests[0].url.queryParameters, isEmpty);
      expect(requests[1].url.path, requests[0].url.path);
      expect(requests[1].url.queryParameters, {'original_images': 'true'});
      expect(requests[2].url.queryParameters, {'limit': '0'});
      expect(requests[3].url.path, '/cards/42/reviews');
      expect(requests[3].url.queryParameters, {
        'limit': '0',
        'original_images': 'true',
      });
    },
  );

  test('uses bookmark endpoints', () async {
    late http.Request request;
    final api = BookmarkApi(
      userId: 'user-1',
      client: MockClient((value) async {
        request = value;
        return http.Response(
          value.method == 'GET' ? '[]' : '{}',
          value.method == 'DELETE' ? 204 : 200,
        );
      }),
    );
    await api.bookmark(1944);
    expect(request.url.path, '/user/user-1/bookmarks/1944');
    await api.unbookmark(1944);
    expect(request.method, 'DELETE');
    expect(request.url.path, '/user/user-1/bookmarks/1944');
    await api.fetchBookmarks(limit: 20, offset: 40);
    expect(request.url.queryParameters, {'limit': '20', 'offset': '40'});
  });

  test('requests the configured recommendation batch size', () async {
    late http.Request request;
    final api = RestaurantApi(
      userId: 'user-1',
      sessionId: 'session-1',
      client: MockClient((value) async {
        request = value;
        return http.Response('[]', 200);
      }),
    );

    await api.fetchCards(limit: 3);

    expect(request.url.queryParameters['limit'], '3');
    expect(request.url.path, '/user/user-1/session/session-1/cards');
  });

  test('loads the decision readiness score', () async {
    late http.Request request;
    final api = RestaurantApi(
      userId: 'user-1',
      sessionId: 'session-1',
      client: MockClient((value) async {
        request = value;
        return http.Response('{"decision_readiness_score":64}', 200);
      }),
    );

    expect(await api.fetchDecisionReadinessScore(), 64);
    expect(
      request.url.path,
      '/user/user-1/session/session-1/decision/metrics/summary',
    );
  });

  test('loads four decision proposal cards by default', () async {
    late http.Request request;
    final api = RestaurantApi(
      userId: 'user-1',
      sessionId: 'session-1',
      client: MockClient((value) async {
        request = value;
        return http.Response(
          '{"ready":true,"proposal_id":"proposal-1","cards":[]}',
          200,
        );
      }),
    );

    final proposal = await api.fetchDecisionProposal();

    expect(proposal.ready, isTrue);
    expect(proposal.proposalId, 'proposal-1');
    expect(request.url.path, '/user/user-1/session/session-1/decision');
    expect(request.url.queryParameters['n'], '4');
  });
}
