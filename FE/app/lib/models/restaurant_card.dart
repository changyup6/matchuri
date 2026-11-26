import 'package:matchuri/models/menu_item.dart';
import 'package:matchuri/models/store.dart';

class RestaurantCard {
  final int cardId;
  final bool? _bookmarked;

  bool get bookmarked => _bookmarked ?? false;
  bool get hasBookmarkState => _bookmarked != null;
  final Store store;
  final String? image;
  final int? distanceMeters;
  final double? latitude;
  final double? longitude;
  final List<String> images;
  final List<MenuItem>? menus;

  RestaurantCard({
    required this.cardId,
    this._bookmarked,
    required this.store,
    required this.image,
    this.distanceMeters,
    this.latitude,
    this.longitude,
    this.images = const [],
    this.menus,
  });

  factory RestaurantCard.fromJson(Map<String, dynamic> json) {
    final store = json['store'] as Map<String, dynamic>;
    return RestaurantCard(
      cardId: json['card_id'] as int,
      bookmarked: json['bookmarked'] as bool?,
      store: Store.fromJson(store),
      image: json['image'] as String?,
      distanceMeters: (json['distance_m'] as num?)?.toInt(),
      latitude: ((json['lat'] ?? store['lat']) as num?)?.toDouble(),
      longitude: ((json['lon'] ?? store['lon']) as num?)?.toDouble(),
      images: (json['images'] as List<dynamic>?)?.cast<String>() ?? const [],
      menus: (json['menus'] as List<dynamic>?)
          ?.map((e) => MenuItem.fromJson(e as Map<String, dynamic>))
          .toList(),
    );
  }
}
