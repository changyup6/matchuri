import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:matchuri/services/api_config.dart';

class SurveyApi {
  final http.Client _client;
  SurveyApi({http.Client? client}) : _client = client ?? http.Client();

  Uri _uri(String userId, String path) =>
      Uri.parse('$apiBaseUrl/user/${Uri.encodeComponent(userId)}/$path');

  Future<void> recordDirection({
    required String userId,
    required int cardId,
    required String mapApp,
    required String mode,
  }) async {
    final response = await _client.post(
      _uri(userId, 'directions'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'card_id': cardId, 'map_app': mapApp, 'mode': mode}),
    ).timeout(const Duration(seconds: 20));
    _check(response);
  }

  Future<bool> submit({
    required String userId,
    required int cardId,
    required bool visited,
    String? satisfaction,
    String? taste,
    String? price,
    String? mood,
    String? notVisitedReason,
    bool? arrived,
  }) async {
    if (visited && (!const ['bad', 'soso', 'good'].contains(satisfaction) ||
        [taste, price, mood].any((s) => s == null || s.trim().isEmpty || s.trim().runes.length > 200))) {
      throw ArgumentError('만족도와 맛·가격·분위기 리뷰를 모두 작성해 주세요.');
    }
    if (!visited && !const ['far', 'no_time', 'changed_mind', 'went_elsewhere'].contains(notVisitedReason)) {
      throw ArgumentError('방문하지 않은 이유를 선택해 주세요.');
    }
    final response = await _client.post(
      _uri(userId, 'surveys'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'card_id': cardId,
        'visited': visited,
        if (visited) ...{
          'satisfaction': satisfaction,
          'review': {'taste': taste!.trim(), 'price': price!.trim(), 'mood': mood!.trim()},
        } else 'not_visited_reason': notVisitedReason,
        'arrived': ?arrived,
      }),
    ).timeout(const Duration(seconds: 20));
    _check(response);
    final body = jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
    if (body['ok'] != true) throw Exception('설문 저장이 완료되지 않았어요.');
    return body['recorded'] == true;
  }

  void _check(http.Response response) {
    if (response.statusCode != 200) {
      throw SurveyApiException(response.statusCode);
    }
  }

  void dispose() => _client.close();
}

class SurveyApiException implements Exception {
  final int status;
  const SurveyApiException(this.status);
  @override
  String toString() => switch (status) {
    404 => '방문 기록을 찾지 못했어요. 식당의 길찾기에서 다시 시작해 주세요.',
    409 => '방문 기록이 변경됐어요. 최신 알림에서 다시 열어 주세요.',
    422 => '입력 내용을 확인해 주세요. 맛·가격·분위기는 각각 200자까지 작성할 수 있어요.',
    _ => '서버에 저장하지 못했어요. 잠시 후 다시 시도해 주세요.',
  };
}
