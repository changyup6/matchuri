class AppUser {
  final String userId;
  final String name;
  final DateTime? createdAt;
  final UserFilter? filter;
  final String? sessionId;

  AppUser({
    required this.userId,
    required this.name,
    this.createdAt,
    this.filter,
    this.sessionId,
  });

  AppUser copyWith({UserFilter? filter, String? sessionId}) => AppUser(
    userId: userId,
    name: name,
    createdAt: createdAt,
    filter: filter ?? this.filter,
    sessionId: sessionId ?? this.sessionId,
  );

  factory AppUser.fromJson(Map<String, dynamic> json) {
    return AppUser(
      userId: json['user_id'] as String,
      name: (json['name'] as String?) ?? '',
      createdAt: DateTime.tryParse((json['created_at'] as String?) ?? ''),
      filter: json['filter'] is Map<String, dynamic>
          ? UserFilter.fromJson(json['filter'] as Map<String, dynamic>)
          : null,
    );
  }
}

class UserFilter {
  final List<String> categories;
  final List<String> tags;
  final int? withinMeters;

  const UserFilter({
    required this.categories,
    required this.tags,
    required this.withinMeters,
  });

  factory UserFilter.fromJson(Map<String, dynamic> json) => UserFilter(
    categories: (json['cat'] as List<dynamic>? ?? const []).cast<String>(),
    tags: (json['tag'] as List<dynamic>? ?? const []).cast<String>(),
    withinMeters: json['within_m'] as int?,
  );
}
