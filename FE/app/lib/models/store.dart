import 'package:matchuri/models/review.dart';

class Store {
  final String id;
  final String name;
  final String location;
  final String category;
  final String? categoryGroup;
  final double? score;
  final List<String> tags;
  final int reviewCount;
  final List<Review> reviews;
  final String? summary;
  final String? subway;
  final String? openStatus;
  final String? parking;

  Store({
    required this.id,
    required this.name,
    required this.location,
    required this.category,
    this.categoryGroup,
    required this.score,
    this.tags = const [],
    this.reviewCount = 0,
    this.reviews = const [],
    this.summary,
    this.subway,
    this.openStatus,
    this.parking,
  });

  factory Store.fromJson(Map<String, dynamic> json) {
    return Store(
      id: json['id'] as String,
      name: json['name'] as String,
      location: json['location'] as String,
      category: json['category'] as String,
      categoryGroup: json['category_group'] as String?,
      score: (json['score'] as num?)?.toDouble(),
      tags: (json['tags'] as List<dynamic>?)?.cast<String>() ?? const [],
      reviewCount: (json['review_count'] as num?)?.toInt() ?? 0,
      reviews:
          (json['reviews'] as List<dynamic>?)
              ?.map((e) => Review.fromJson(e as Map<String, dynamic>))
              .toList() ??
          const [],
      summary: json['summary'] as String?,
      subway: json['subway'] as String?,
      openStatus: json['open_status'] as String?,
      parking: json['parking'] as String?,
    );
  }
}
