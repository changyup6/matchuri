import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:matchuri/services/preferences_api.dart';

void main() {
  test('saves the selected filters with the API field names', () async {
    late http.Request request;
    final service = PreferencesApi(
      client: MockClient((value) async {
        request = value;
        return http.Response('', 204);
      }),
    );

    await service.saveFilters(
      userId: 'user-1',
      sessionId: 'session-1',
      categories: ['일식'],
      tags: ['혼밥'],
      withinMeters: 500,
    );

    expect(request.method, 'PUT');
    expect(request.url.path, '/user/user-1/session/session-1/filter');
    expect(jsonDecode(request.body), {
      'cat': ['일식'],
      'tag': ['혼밥'],
      'within_m': 500,
    });
  });

  test('saves the current location with lat and lon', () async {
    late http.Request request;
    final service = PreferencesApi(
      client: MockClient((value) async {
        request = value;
        return http.Response('{}', 200);
      }),
    );

    await service.saveLocation(
      userId: 'user-1',
      sessionId: 'session-1',
      latitude: 37.5006,
      longitude: 127.0366,
    );

    expect(request.method, 'PUT');
    expect(request.url.path, '/user/user-1/session/session-1/location');
    expect(jsonDecode(request.body), {'lat': 37.5006, 'lon': 127.0366});
  });

  test(
    'accepts a no-content response when saving the current location',
    () async {
      final service = PreferencesApi(
        client: MockClient((_) async => http.Response('', 204)),
      );

      await service.saveLocation(
        userId: 'user-1',
        sessionId: 'session-1',
        latitude: 37.5006,
        longitude: 127.0366,
      );
    },
  );
}
