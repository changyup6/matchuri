import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:matchuri/models/app_user.dart';
import 'package:matchuri/services/api_config.dart';

class UserApi {
  final http.Client _client;
  UserApi({http.Client? client}) : _client = client ?? http.Client();
  Future<AppUser> register(String name) async {
    final response = await _client.post(
      Uri.parse('$apiBaseUrl/user'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'name': name}),
    );
    if (response.statusCode != 200 && response.statusCode != 201) {
      throw Exception('Failed to register user: ${response.statusCode}');
    }
    return AppUser.fromJson(
      jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>,
    );
  }

  Future<AppUser> fetchUser(String userId) async {
    final response = await _client.get(Uri.parse('$apiBaseUrl/user/$userId'));
    if (response.statusCode != 200) {
      throw Exception('Failed to load user: ${response.statusCode}');
    }
    return AppUser.fromJson(
      jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>,
    );
  }

  Future<String> createSession(String userId) async {
    final response = await _client.post(
      Uri.parse('$apiBaseUrl/user/$userId/session'),
    );
    if (response.statusCode != 200) {
      throw Exception('Failed to start session: ${response.statusCode}');
    }
    return (jsonDecode(utf8.decode(response.bodyBytes))
            as Map<String, dynamic>)['session_id']
        as String;
  }

  Future<String> lookupLegacySessionUserId(String sessionId) async {
    final response = await _client.get(
      Uri.parse('$apiBaseUrl/session/$sessionId'),
    );
    if (response.statusCode != 200) {
      throw Exception('Failed to migrate session: ${response.statusCode}');
    }
    return (jsonDecode(utf8.decode(response.bodyBytes))
            as Map<String, dynamic>)['user_id']
        as String;
  }

  void dispose() => _client.close();
}
