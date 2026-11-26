class TasteMap {
  final int swipes;
  final int likes;
  final List<TasteRegion> regions;
  final List<TasteTrailPoint> trail;
  final TasteNow? now;
  final TasteDrift? drift;
  final String headline;

  const TasteMap({
    required this.swipes,
    required this.likes,
    required this.regions,
    required this.trail,
    this.now,
    this.drift,
    required this.headline,
  });

  factory TasteMap.fromJson(
    Map<String, dynamic> json, {
    List<TasteRegion>? regions,
  }) {
    final map = json['map'] as Map<String, dynamic>? ?? const {};
    return TasteMap(
      swipes: json['swipes'] as int? ?? 0,
      likes: json['likes'] as int? ?? 0,
      regions:
          regions ??
          (map['regions'] as List<dynamic>? ?? const [])
              .whereType<Map<String, dynamic>>()
              .map(TasteRegion.fromJson)
              .toList(),
      trail: (json['trail'] as List<dynamic>? ?? const [])
          .whereType<Map<String, dynamic>>()
          .map(TasteTrailPoint.fromJson)
          .toList(),
      now: json['now'] is Map<String, dynamic>
          ? TasteNow.fromJson(json['now'] as Map<String, dynamic>)
          : null,
      drift: json['drift'] is Map<String, dynamic>
          ? TasteDrift.fromJson(json['drift'] as Map<String, dynamic>)
          : null,
      headline: json['headline'] as String? ?? '',
    );
  }
}

class TasteRegion {
  final String name;
  final double x;
  final double y;
  final double radius;
  final int count;
  const TasteRegion({
    required this.name,
    required this.x,
    required this.y,
    required this.radius,
    required this.count,
  });
  factory TasteRegion.fromJson(Map<String, dynamic> json) => TasteRegion(
    name: json['name'] as String? ?? '',
    x: (json['x'] as num? ?? 0).toDouble(),
    y: (json['y'] as num? ?? 0).toDouble(),
    radius: (json['r'] as num? ?? 0).toDouble(),
    count: json['count'] as int? ?? 0,
  );
}

class TasteMapChildren {
  final String level;
  final List<TasteMapNode> nodes;

  const TasteMapChildren({required this.level, required this.nodes});

  factory TasteMapChildren.fromJson(Map<String, dynamic> json) =>
      TasteMapChildren(
        level: json['level'] as String? ?? 'cat2',
        nodes: (json['nodes'] as List<dynamic>? ?? const [])
            .whereType<Map<String, dynamic>>()
            .map(TasteMapNode.fromJson)
            .toList(),
      );
}

class TasteMapNode {
  final String key;
  final String name;
  final double x;
  final double y;
  final double radius;
  final int count;
  final int? cardId;

  const TasteMapNode({
    required this.key,
    required this.name,
    required this.x,
    required this.y,
    required this.radius,
    required this.count,
    this.cardId,
  });

  factory TasteMapNode.fromJson(Map<String, dynamic> json) => TasteMapNode(
    key: json['key'] as String? ?? '',
    name: json['name'] as String? ?? '',
    x: (json['x'] as num? ?? 0).toDouble(),
    y: (json['y'] as num? ?? 0).toDouble(),
    radius: (json['r'] as num? ?? 0).toDouble(),
    count: (json['count'] as num? ?? 0).toInt(),
    cardId: (json['card_id'] as num?)?.toInt(),
  );
}

class TasteNearbyRestaurant {
  final int? cardId;
  final String storeId;
  final String name;
  final String category;
  final String location;
  final String? image;

  const TasteNearbyRestaurant({
    this.cardId,
    required this.storeId,
    required this.name,
    required this.category,
    required this.location,
    this.image,
  });

  factory TasteNearbyRestaurant.fromJson(Map<String, dynamic> json) {
    final store = json['store'] as Map<String, dynamic>? ?? const {};
    return TasteNearbyRestaurant(
      cardId: (json['card_id'] as num?)?.toInt(),
      storeId: (json['store_id'] ?? store['id'])?.toString() ?? '',
      name: (json['name'] ?? store['name'])?.toString() ?? '',
      category: (json['category'] ?? store['category'])?.toString() ?? '',
      location: (json['location'] ?? store['location'])?.toString() ?? '',
      image: json['image'] as String?,
    );
  }
}

class TasteTrailPoint {
  final double x;
  final double y;
  final String region;
  const TasteTrailPoint({required this.x, required this.y, this.region = ''});
  factory TasteTrailPoint.fromJson(Map<String, dynamic> json) =>
      TasteTrailPoint(
        x: (json['x'] as num? ?? 0).toDouble(),
        y: (json['y'] as num? ?? 0).toDouble(),
        region: json['region'] as String? ?? '',
      );
}

class TasteNow extends TasteTrailPoint {
  final List<TasteNearbyRestaurant> near;
  const TasteNow({
    required super.x,
    required super.y,
    required super.region,
    this.near = const [],
  });
  factory TasteNow.fromJson(Map<String, dynamic> json) => TasteNow(
    x: (json['x'] as num? ?? 0).toDouble(),
    y: (json['y'] as num? ?? 0).toDouble(),
    region: json['region'] as String? ?? '',
    near: (json['near'] as List<dynamic>? ?? const [])
        .whereType<Map<String, dynamic>>()
        .map(TasteNearbyRestaurant.fromJson)
        .toList(),
  );
}

class TasteDrift {
  final String fromRegion;
  final String toRegion;
  final int window;
  const TasteDrift({
    required this.fromRegion,
    required this.toRegion,
    required this.window,
  });
  factory TasteDrift.fromJson(Map<String, dynamic> json) => TasteDrift(
    fromRegion: json['from_region'] as String? ?? '',
    toRegion: json['to_region'] as String? ?? '',
    window: json['window'] as int? ?? 0,
  );
}
