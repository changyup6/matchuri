class Review {
  final int id;
  final String storeId;
  final String content;
  final double? score;
  final List<String> images;
  final String? reviewerId;
  final DateTime? date;
  final List<String> tags;
  final int likeCount;

  Review({
    required this.id,
    required this.storeId,
    required this.content,
    required this.score,
    this.images = const [],
    this.reviewerId,
    this.date,
    this.tags = const [],
    this.likeCount = 0,
  });

  factory Review.fromJson(Map<String, dynamic> json) {
    return Review(
      id: json['id'] as int,
      storeId: json['store_id'] as String,
      content: (json['content'] as String?) ?? '',
      score: (json['score'] as num?)?.toDouble(),
      images:
          (json['images'] as List<dynamic>?)?.cast<String>() ?? const [],
      reviewerId: json['reviewer_id'] as String?,
      date: DateTime.tryParse((json['date'] as String?) ?? ''),
      tags: (json['tags'] as List<dynamic>?)?.cast<String>() ?? const [],
      likeCount: (json['like_count'] as num?)?.toInt() ?? 0,
    );
  }
}
