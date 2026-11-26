import 'package:flutter_test/flutter_test.dart';
import 'package:matchuri/models/store.dart';

void main() {
  test('parses bookmark category_group separately from category', () {
    final store = Store.fromJson({
      'id': '1',
      'name': 'Restaurant',
      'location': 'Seoul',
      'category': 'Other',
      'category_group': 'Japanese',
    });

    expect(store.categoryGroup, 'Japanese');
  });
}
