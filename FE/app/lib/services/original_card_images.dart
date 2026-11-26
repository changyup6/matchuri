import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:matchuri/models/restaurant_card.dart';
import 'package:matchuri/models/review.dart';
import 'package:matchuri/services/restaurant_detail_api.dart';

/// Original URLs are requested only when a photo viewer opens.
/// A successful detail response is shared by viewers on this card's screen.
class OriginalCardImages {
  final RestaurantDetailApi api;
  final int cardId;
  Future<RestaurantCard>? _detail;
  Future<List<Review>>? _reviews;

  OriginalCardImages({required this.api, required this.cardId});

  Future<String> _resolve(
    String thumbnail,
    String? Function(RestaurantCard) select,
  ) async {
    try {
      final detail = await (_detail ??= api.fetchCardDetail(
        cardId,
        originalImages: true,
      ));
      if (detail.cardId != cardId) {
        throw StateError('식당 정보가 일치하지 않습니다.');
      }
      final url = select(detail);
      if (url == null || url.isEmpty || !_samePhoto(thumbnail, url)) {
        throw StateError('이 사진의 원본을 찾을 수 없습니다.');
      }
      return url;
    } catch (_) {
      // A retry must make a fresh request after a failed lookup or API call.
      _detail = null;
      rethrow;
    }
  }

  bool _samePhoto(String thumbnail, String original) {
    if (thumbnail == original) return true;
    final path = Uri.tryParse(thumbnail)?.path ?? '';
    if (!path.startsWith('/media/catalog/')) return false;
    final filename = path.split('/').last;
    return filename == '${sha256.convert(utf8.encode(original))}.jpg';
  }

  Future<String> hero(String thumbnail) =>
      _resolve(thumbnail, (detail) => detail.image);

  Future<String> gallery(int index, String thumbnail) => _resolve(
    thumbnail,
    (detail) => index >= 0 && index < detail.images.length
        ? detail.images[index]
        : null,
  );

  Future<String> menu(int menuId, String thumbnail) =>
      _resolve(thumbnail, (detail) {
        for (final menu in detail.menus ?? []) {
          if (menu.id == menuId) return menu.image;
        }
        return null;
      });

  String? _reviewPhoto(List<Review> reviews, int reviewId, int imageIndex) {
    for (final review in reviews) {
      if (review.id == reviewId) {
        return imageIndex >= 0 && imageIndex < review.images.length
            ? review.images[imageIndex]
            : null;
      }
    }
    return null;
  }

  Future<String> review(int reviewId, int imageIndex, String thumbnail) async {
    if (_reviews == null) {
      try {
        return await _resolve(
          thumbnail,
          (detail) => _reviewPhoto(detail.store.reviews, reviewId, imageIndex),
        );
      } catch (_) {
        // The detail response contains a preview. A tapped review from “more
        // reviews” needs the originals endpoint for the full list instead.
      }
    }
    try {
      final reviews = await (_reviews ??= api.fetchReviews(
        cardId,
        originalImages: true,
      ));
      final url = _reviewPhoto(reviews, reviewId, imageIndex);
      if (url == null || url.isEmpty || !_samePhoto(thumbnail, url)) {
        throw StateError('이 사진의 원본을 찾을 수 없습니다.');
      }
      return url;
    } catch (_) {
      _reviews = null;
      rethrow;
    }
  }
}
