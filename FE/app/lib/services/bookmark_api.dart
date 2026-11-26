import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;
import 'package:matchuri/models/restaurant_card.dart';
import 'package:matchuri/services/api_config.dart';

class _BookmarkState {
  final Map<int, bool> changes = {};
  final Map<int, Future<void>> mutations = {};
  final Set<BookmarkApi> clients = {};
}

class BookmarkApi extends ChangeNotifier {
  static final Map<String, _BookmarkState> _users = {};

  final String userId;
  final http.Client _client;
  final _BookmarkState _state;
  bool _disposed = false;

  BookmarkApi({required this.userId, http.Client? client})
    : _client = client ?? http.Client(),
      _state = _users.putIfAbsent(userId, _BookmarkState.new) {
    _state.clients.add(this);
  }

  // Successful changes override prefetched snapshots across screens and clients.
  // A server-provided false is authoritative; a missing field uses the old hint.
  bool isBookmarked(RestaurantCard card, {bool fallback = false}) =>
      _state.changes[card.cardId] ??
      (card.hasBookmarkState ? card.bookmarked : fallback);

  void _changed(int cardId, bool value) {
    _state.changes[cardId] = value;
    for (final client in _state.clients.toList()) {
      if (!client._disposed) client.notifyListeners();
    }
  }

  Future<void> bookmark(int cardId) => _mutate(cardId, true);

  Future<void> unbookmark(int cardId) => _mutate(cardId, false);

  Future<void> _mutate(int cardId, bool value) {
    if (_disposed) return Future.error(StateError('BookmarkApi is disposed'));
    // Serialize only this user's mutations of the same card, including those
    // from other screens. Ignoring old responses alone cannot order server writes.
    final previous = _state.mutations[cardId] ?? Future<void>.value();
    final operation = previous.then((_) async {
      if (_disposed) throw StateError('BookmarkApi is disposed');
      final uri = Uri.parse('$apiBaseUrl/user/$userId/bookmarks/$cardId');
      final response = value
          ? await _client.put(uri)
          : await _client.delete(uri);
      final success = value
          ? response.statusCode == 200
          : response.statusCode >= 200 && response.statusCode < 300;
      if (!success) {
        throw Exception(
          'Failed to ${value ? 'bookmark' : 'remove bookmark from'} restaurant: '
          '${response.statusCode}',
        );
      }
      _changed(cardId, value);
    });
    // Each caller retains its failure; a failed request must not block the next.
    final settled = operation.then<void>(
      (_) {},
      onError: (Object _, StackTrace _) {},
    );
    _state.mutations[cardId] = settled;
    unawaited(
      settled.then((_) {
        if (identical(_state.mutations[cardId], settled)) {
          _state.mutations.remove(cardId);
        }
        _releaseIfUnused();
      }),
    );
    return operation;
  }

  void _releaseIfUnused() {
    if (_state.clients.isEmpty &&
        _state.mutations.isEmpty &&
        identical(_users[userId], _state)) {
      _users.remove(userId);
    }
  }

  Future<List<RestaurantCard>> fetchBookmarks({
    int limit = 50,
    int offset = 0,
  }) async {
    final response = await _client.get(
      Uri.parse(
        '$apiBaseUrl/user/$userId/bookmarks?limit=$limit&offset=$offset',
      ),
    );
    if (response.statusCode != 200) {
      throw Exception('Failed to load bookmarks: ${response.statusCode}');
    }
    return (jsonDecode(utf8.decode(response.bodyBytes)) as List<dynamic>)
        .map((json) => RestaurantCard.fromJson(json as Map<String, dynamic>))
        .toList();
  }

  @override
  void dispose() {
    if (_disposed) return;
    _disposed = true;
    _state.clients.remove(this);
    _releaseIfUnused();
    _client.close();
    super.dispose();
  }
}
