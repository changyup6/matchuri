import 'package:location/location.dart';

class LocationService {
  final Location _location = Location();

  Future<({double lat, double lng})?> getCurrentLocation() async {
    if (!await _location.serviceEnabled() && !await _location.requestService()) {
      return null;
    }

    var permission = await _location.hasPermission();
    if (permission == PermissionStatus.denied) {
      permission = await _location.requestPermission();
    }
    if (permission != PermissionStatus.granted &&
        permission != PermissionStatus.grantedLimited) {
      return null;
    }

    final data = await _location.getLocation();
    return (lat: data.latitude, lng: data.longitude);
  }
}
