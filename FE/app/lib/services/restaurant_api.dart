import 'dart:convert';

import 'package:matchuri/models/decision_proposal.dart';
import 'package:http/http.dart' as http;
import 'package:matchuri/models/restaurant_card.dart';
import 'package:matchuri/services/api_config.dart';

class RestaurantApi {
  final String userId;
  final String sessionId;
  final http.Client _client;

  RestaurantApi({
    required this.userId,
    required this.sessionId,
    http.Client? client,
  }) : _client = client ?? http.Client();

  Future<List<RestaurantCard>> fetchCards({int limit = 50}) async {
    final response = await _client.get(
      Uri.parse(
        '$apiBaseUrl/user/$userId/session/$sessionId/cards?limit=$limit&reviews=1',
      ),
    );
    if (response.statusCode != 200) {
      throw Exception(
        'Failed to load restaurant cards: ${response.statusCode}',
      );
    }
    return (jsonDecode(utf8.decode(response.bodyBytes)) as List<dynamic>)
        .map((json) => RestaurantCard.fromJson(json as Map<String, dynamic>))
        .toList();
  }

  Future<Map<String, dynamic>> postSwipeLog({
    required int cardId,
    required String prefer,
  }) async {
    final response = await _client.post(
      Uri.parse('$apiBaseUrl/user/$userId/session/$sessionId/preferences'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'card_id': cardId, 'prefer': prefer}),
    );
    if (response.statusCode != 200) {
      throw Exception('Failed to save swipe: ${response.statusCode}');
    }
    return jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
  }

  Future<double> fetchDecisionReadinessScore() async {
    final response = await _client.get(
      Uri.parse(
        '$apiBaseUrl/user/$userId/session/$sessionId/decision/metrics/summary',
      ),
    );
    if (response.statusCode != 200) {
      throw Exception(
        'Failed to load decision readiness: ${response.statusCode}',
      );
    }
    final json =
        jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
    // Keep the server's fractional changes.  This score is updated after every
    // swipe, and truncating it to an integer hides small but meaningful changes
    // from the user.
    return (json['decision_readiness_score'] as num).toDouble();
  }

  Future<DecisionProposal> fetchDecisionProposal({int count = 4}) async {
    final response = await _client.get(
      Uri.parse(
        '$apiBaseUrl/user/$userId/session/$sessionId/decision?n=$count',
      ),
    );
    if (response.statusCode != 200) {
      throw Exception(
        'Failed to load decision proposal: ${response.statusCode}',
      );
    }
    return DecisionProposal.fromJson(
      jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>,
    );
  }

  void dispose() => _client.close();
}
