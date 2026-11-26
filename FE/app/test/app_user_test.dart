import 'package:flutter_test/flutter_test.dart';
import 'package:matchuri/models/app_user.dart';

void main() {
  test('keeps a null distance filter as distance-unlimited', () {
    final filter = UserFilter.fromJson({
      'cat': [],
      'tag': [],
      'within_m': null,
    });

    expect(filter.withinMeters, isNull);
  });
}
