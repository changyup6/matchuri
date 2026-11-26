import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:matchuri/services/api_config.dart';

class FilterOptions {
  final List<String> categories;
  final List<String> tags;
  const FilterOptions({required this.categories, required this.tags});
}

class PreferencesApi {
  final http.Client _client;
  PreferencesApi({http.Client? client}) : _client = client ?? http.Client();
  Future<FilterOptions> loadFilterOptions() async {
    final response = await _client.get(Uri.parse('$apiBaseUrl/filters'));
    if (response.statusCode != 200) {
      throw Exception('Failed to load filters: ${response.statusCode}');
    }
    final json =
        jsonDecode(utf8.decode(response.bodyBytes)) as Map<String, dynamic>;
    List<String> names(String key) => (json[key] as List<dynamic>)
        .map((item) => (item as Map<String, dynamic>)['name'] as String)
        .toList();
    return FilterOptions(categories: names('cats'), tags: names('tags'));
  }

  Future<void> saveFilters({
    required String userId,
    required String sessionId,
    required Iterable<String> categories,
    required Iterable<String> tags,
    int? withinMeters,
  }) async {
    final response = await _client.put(
      Uri.parse('$apiBaseUrl/user/$userId/session/$sessionId/filter'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({
        'cat': categories.toList(),
        'tag': tags.toList(),
        'within_m': withinMeters,
      }),
    );
    if (response.statusCode != 200 && response.statusCode != 204) {
      throw Exception('Failed to save filters: ${response.statusCode}');
    }
  }

  Future<void> saveLocation({
    required String userId,
    required String sessionId,
    required double latitude,
    required double longitude,
  }) async {
    final response = await _client.put(
      Uri.parse('$apiBaseUrl/user/$userId/session/$sessionId/location'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'lat': latitude, 'lon': longitude}),
    );
    if (response.statusCode != 200 && response.statusCode != 204) {
      throw Exception('Failed to save location: ${response.statusCode}');
    }
  }

  void dispose() => _client.close();
}
