import 'package:flutter/services.dart';
import 'dart:convert';
import 'package:matchuri/models/visit_review.dart';

class VisitGeofenceService {
  static const _channel = MethodChannel('matchuri/visit_geofence');
  // Current on-device demo: no location or 40-minute wait required.
  static const simulateArrival = bool.fromEnvironment('SIMULATE_VISIT', defaultValue: true);

  static void onReviewAvailable(Future<void> Function()? handler) {
    _channel.setMethodCallHandler(handler == null ? null : (call) async {
      if (call.method == 'reviewAvailable') await handler();
    });
  }

  static Future<VisitReviewTarget?> consumeReviewLaunch() async {
    try {
      final json = await _channel.invokeMethod<String>('consumeReviewLaunch');
      return json == null ? null : VisitReviewTarget.fromJson(
        jsonDecode(json) as Map<String, dynamic>);
    } on MissingPluginException {
      return null;
    }
  }

  static Future<String> register({
    required String userId,
    required int cardId,
    required String storeName,
    double? latitude,
    double? longitude,
  }) async {
    try {
      if (!simulateArrival && (latitude == null || longitude == null)) return 'coordinatesMissing';
      return await _channel.invokeMethod<String>(simulateArrival ? 'simulateVisit' : 'register', {
            'userId': userId,
            'cardId': cardId,
            'visitId': DateTime.now().microsecondsSinceEpoch.toString(),
            'storeName': storeName,
            'latitude': latitude,
            'longitude': longitude,
          }) ??
          'registrationFailed';
    } on PlatformException {
      return 'registrationFailed';
    } on MissingPluginException {
      return 'unsupported';
    }
  }

  static Future<void> openSettings() => _channel.invokeMethod('openSettings');

  static Future<void> requestPermissions() =>
      _channel.invokeMethod('requestVisitPermissions');
}
