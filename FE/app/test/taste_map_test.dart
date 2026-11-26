import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:matchuri/models/taste_map.dart';
import 'package:matchuri/services/taste_api.dart';

void main() {
  test('취향 지형도 응답을 파싱한다', () {
    final taste = TasteMap.fromJson({
      'swipes': 296,
      'likes': 160,
      'map': {
        'regions': [
          {'name': '한식', 'x': .8, 'y': .5, 'r': .13, 'count': 620},
        ],
      },
      'trail': [
        {'x': .5, 'y': .6, 'region': '양식'},
      ],
      'now': {
        'x': .5,
        'y': .6,
        'region': '양식',
        'near': [
          {'card_id': 7, 'name': '파스타집', 'category': '양식'},
        ],
      },
      'drift': {'from_region': '카페', 'to_region': '양식', 'window': 10},
      'headline': '요약',
    });
    expect(taste.regions.single.name, '한식');
    expect(taste.now!.region, '양식');
    expect(taste.now!.near.single.cardId, 7);
    expect(taste.drift!.window, 10);
    expect(taste.trail.single.region, '양식');
  });

  test('loads the user-wide taste map across sessions', () async {
    final paths = <String>[];
    final api = TasteApi(
      client: MockClient((request) async {
        paths.add(request.url.path);
        final body = {
          'swipes': 10,
          'likes': 4,
          'map': {
            'regions': [
              {'name': '카페', 'x': .3, 'y': .4, 'r': .2, 'count': 8},
            ],
          },
          'trail': [
            {'x': .3, 'y': .4},
          ],
          'now': {'x': .3, 'y': .4, 'region': '카페'},
          'headline': '카페 취향이 선명해졌어요.',
        };
        return http.Response.bytes(utf8.encode(jsonEncode(body)), 200);
      }),
    );

    final taste = await api.fetchTaste('user-1', 'session-1');

    expect(paths, contains('/users/user-1/taste'));
    expect(taste.regions.single.name, '카페');
    expect(taste.trail, hasLength(1));
  });

  test(
    'merges session tastes when the user-wide compatibility route is missing',
    () async {
      final api = TasteApi(
        client: MockClient((request) async {
          if (request.url.path == '/users/user-1/taste') {
            return http.Response('{"detail":"session not found"}', 404);
          }
          if (request.url.path == '/user/user-1/session') {
            return http.Response(
              jsonEncode([
                {'user_id': 'user-1', 'session_id': 'old', 'swipes': 2},
                {'user_id': 'user-1', 'session_id': 'current', 'swipes': 1},
              ]),
              200,
            );
          }
          final isCurrent = request.url.path.contains('/current/');
          final body = {
            'swipes': isCurrent ? 1 : 2,
            'likes': isCurrent ? 1 : 1,
            'map': {
              'regions': [
                {'name': '한식', 'x': .2, 'y': .3, 'r': .1, 'count': 10},
              ],
            },
            'trail': [
              {
                'x': isCurrent ? .4 : .2,
                'y': .3,
                'region': isCurrent ? '일식' : '한식',
              },
            ],
            'now': {
              'x': isCurrent ? .4 : .2,
              'y': .3,
              'region': isCurrent ? '일식' : '한식',
            },
            'headline': isCurrent ? '일식 취향이 보여요.' : '한식을 좋아해요.',
          };
          return http.Response.bytes(utf8.encode(jsonEncode(body)), 200);
        }),
      );

      final taste = await api.fetchTaste('user-1', 'current');

      expect(taste.swipes, 3);
      expect(taste.likes, 2);
      expect(taste.trail, hasLength(2));
      expect(taste.now!.region, '일식');
    },
  );

  test('loads a detailed taste-map layer with its parent key', () async {
    late Uri requestedUri;
    final api = TasteApi(
      client: MockClient((request) async {
        requestedUri = request.url;
        return http.Response.bytes(
          utf8.encode(
            jsonEncode({
              'level': 'cat3',
              'nodes': [
                {
                  'key': 'cat3.soup',
                  'name': '찌개',
                  'x': .2,
                  'y': .4,
                  'r': .05,
                  'count': 12,
                  'card_id': null,
                },
              ],
            }),
          ),
          200,
        );
      }),
    );

    final layer = await api.fetchMapChildren(parent: 'cat2.korean');

    expect(requestedUri.path, '/taste/map/children');
    expect(requestedUri.queryParameters['parent'], 'cat2.korean');
    expect(layer.level, 'cat3');
    expect(layer.nodes.single.name, '찌개');
  });
}
