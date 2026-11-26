import 'package:flutter_test/flutter_test.dart';
import 'package:matchuri/models/restaurant_card.dart';

void main() {
  test('parses distance_m from a card response', () {
    final card = RestaurantCard.fromJson({
      'card_id': 1,
      'store': {
        'id': 'store-1',
        'name': '식당',
        'location': '서울',
        'category': '한식',
      },
      'distance_m': 344,
      'lat': 37.5006,
      'lon': 127.0366,
    });

    expect(card.distanceMeters, 344);
    expect(card.latitude, 37.5006);
    expect(card.longitude, 127.0366);
  });
}
