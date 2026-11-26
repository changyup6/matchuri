import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:matchuri/services/user_api.dart';

void main() {
  test('loads an actual user from the session API', () async {
    late http.Request request;
    final api = UserApi(
      client: MockClient((value) async {
        request = value;
        return http.Response.bytes(
          utf8.encode(jsonEncode({'user_id': 'u-1', 'name': 'test'})),
          200,
          headers: {'content-type': 'application/json; charset=utf-8'},
        );
      }),
    );

    final user = await api.fetchUser('u-1');

    expect(request.url.path, '/user/u-1');
    expect(user.userId, 'u-1');
    expect(user.name, 'test');
  });
}
