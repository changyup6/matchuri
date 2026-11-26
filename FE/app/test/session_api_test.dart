import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:matchuri/services/user_api.dart';

void main() {
  test('creates a fresh recommendation session for a user', () async {
    late http.Request request;
    final api = UserApi(
      client: MockClient((value) async {
        request = value;
        return http.Response(
          jsonEncode({
            'user_id': 'user-1',
            'session_id': 'session-1',
            'swipes': 0,
          }),
          200,
        );
      }),
    );

    expect(await api.createSession('user-1'), 'session-1');
    expect(request.method, 'POST');
    expect(request.url.path, '/user/user-1/session');
  });

  test('resolves a stored legacy session to its actual user', () async {
    late http.Request request;
    final api = UserApi(
      client: MockClient((value) async {
        request = value;
        return http.Response(
          jsonEncode({
            'user_id': 'user-1',
            'session_id': 'legacy-1',
            'swipes': 3,
          }),
          200,
        );
      }),
    );

    expect(await api.lookupLegacySessionUserId('legacy-1'), 'user-1');
    expect(request.url.path, '/session/legacy-1');
  });
}
