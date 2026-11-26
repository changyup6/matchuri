import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:matchuri/models/restaurant_card.dart';
import 'package:matchuri/models/review.dart';
import 'package:matchuri/services/api_config.dart';

class RestaurantDetailApi {
  final String userId;
  final String sessionId;
  final http.Client _client;

  RestaurantDetailApi({
    required this.userId,
    required this.sessionId,
    http.Client? client,
  }) : _client = client ?? http.Client();

  Future<RestaurantCard> fetchCardDetail(
    int cardId, {
    bool originalImages = false,
  }) async {
    final response = await _client.get(
      Uri.parse(
        '$apiBaseUrl/user/$userId/session/$sessionId/cards/$cardId',
      ).replace(
        queryParameters: originalImages ? {'original_images': 'true'} : null,
      ),
    );
    if (response.statusCode != 200) {
      throw Exception(
        'Failed to load restaurant details: ${response.statusCode}',
      );
    }
    return RestaurantCard.fromJson(
      jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>,
    );
  }

  Future<List<Review>> fetchReviews(
    int cardId, {
    int limit = 0,
    bool originalImages = false,
  }) async {
    final response = await _client.get(
      Uri.parse('$apiBaseUrl/cards/$cardId/reviews').replace(
        queryParameters: {
          'limit': '$limit',
          if (originalImages) 'original_images': 'true',
        },
      ),
    );
    if (response.statusCode != 200) {
      throw Exception('Failed to load reviews: ${response.statusCode}');
    }
    return (jsonDecode(utf8.decode(response.bodyBytes)) as List<dynamic>)
        .map((json) => Review.fromJson(json as Map<String, dynamic>))
        .toList();
  }

  void dispose() => _client.close();
}
