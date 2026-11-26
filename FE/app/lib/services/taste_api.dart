import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:matchuri/models/taste_map.dart';
import 'package:matchuri/services/api_config.dart';

class TasteApi {
  final http.Client _client;
  TasteApi({http.Client? client}) : _client = client ?? http.Client();

  Future<TasteMap> fetchTaste(String userId, String currentSessionId) async {
    final response = await _client.get(
      Uri.parse('$apiBaseUrl/users/$userId/taste'),
    );
    if (response.statusCode == 200) {
      return _parseTaste(response);
    }

    // Some deployed backends still treat /users/{id}/taste as a legacy
    // session-id endpoint. Reconstruct the user's accumulated taste from the
    // explicit session API when that compatibility route cannot find the user.
    if (response.statusCode == 404) {
      return _fetchTasteAcrossSessions(userId, currentSessionId);
    }
    throw Exception('Failed to load taste: ${response.statusCode}');
  }

  TasteMap _parseTaste(http.Response response) => TasteMap.fromJson(
    jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>,
  );

  Future<TasteMap> _fetchTasteAcrossSessions(
    String userId,
    String currentSessionId,
  ) async {
    final sessionsResponse = await _client.get(
      Uri.parse('$apiBaseUrl/user/$userId/session'),
    );
    if (sessionsResponse.statusCode != 200) {
      throw Exception(
        'Failed to load taste sessions: ${sessionsResponse.statusCode}',
      );
    }

    final sessions =
        (jsonDecode(utf8.decode(sessionsResponse.bodyBytes)) as List<dynamic>)
            .whereType<Map<String, dynamic>>()
            .where(
              (session) =>
                  (session['swipes'] as num? ?? 0) > 0 ||
                  session['session_id'] == currentSessionId,
            )
            .toList();
    sessions.sort(
      (left, right) => left['session_id'] == currentSessionId
          ? 1
          : right['session_id'] == currentSessionId
          ? -1
          : 0,
    );

    final tastes = <TasteMap>[];
    for (final session in sessions) {
      final sessionId = session['session_id'] as String?;
      if (sessionId == null) continue;
      final sessionResponse = await _client.get(
        Uri.parse('$apiBaseUrl/user/$userId/session/$sessionId/taste'),
      );
      if (sessionResponse.statusCode == 200) {
        tastes.add(_parseTaste(sessionResponse));
      } else if (sessionResponse.statusCode != 404) {
        throw Exception(
          'Failed to load session taste: ${sessionResponse.statusCode}',
        );
      }
    }
    if (tastes.isEmpty) {
      throw Exception('No taste data found for user');
    }
    return _mergeTastes(tastes);
  }

  TasteMap _mergeTastes(List<TasteMap> tastes) {
    final trail = tastes.expand((taste) => taste.trail).toList();
    final recentTrail = trail.length <= 60
        ? trail
        : trail.sublist(trail.length - 60);
    final latest = tastes.last;
    final regions = tastes
        .map((taste) => taste.regions)
        .firstWhere((regions) => regions.isNotEmpty, orElse: () => const []);
    return TasteMap(
      swipes: tastes.fold(0, (total, taste) => total + taste.swipes),
      likes: tastes.fold(0, (total, taste) => total + taste.likes),
      regions: regions,
      trail: recentTrail,
      now: tastes.reversed
          .map((taste) => taste.now)
          .firstWhere((now) => now != null, orElse: () => null),
      drift: latest.drift,
      headline: tastes.reversed
          .map((taste) => taste.headline)
          .firstWhere((headline) => headline.isNotEmpty, orElse: () => ''),
    );
  }

  Future<TasteMapChildren> fetchMapChildren({String? parent}) async {
    final uri = Uri.parse('$apiBaseUrl/taste/map/children')
        .replace(queryParameters: parent == null ? null : {'parent': parent});
    final response = await _client.get(uri);
    if (response.statusCode != 200) {
      throw Exception(
        'Failed to load taste map children: ${response.statusCode}',
      );
    }
    return TasteMapChildren.fromJson(
      jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>,
    );
  }

  void dispose() => _client.close();
}
