import 'package:flutter/foundation.dart';
import 'package:geocoding/geocoding.dart';
import 'package:url_launcher/url_launcher.dart';

/// 길찾기에 쓰는 이동 수단.
enum DirectionsMode { walk, transit, car }

extension DirectionsModeLabel on DirectionsMode {
  String get label => switch (this) {
        DirectionsMode.walk => '도보',
        DirectionsMode.transit => '대중교통',
        DirectionsMode.car => '자동차',
      };
}

/// 딥링크를 걸 수 있는 지도 앱.
enum MapApp { naver, kakao, google }

extension MapAppLabel on MapApp {
  String get label => switch (this) {
        MapApp.naver => '네이버지도',
        MapApp.kakao => '카카오맵',
        MapApp.google => '구글 지도',
      };
}

/// 앱 이름을 넘겨야 하는 네이버지도 딥링크용 식별자.
const _appId = 'com.matchuri';

/// 주소 문자열 뒤에 붙은 층/호수/괄호 설명을 떼어낸다.
/// 지오코더가 "지하 1층", "3층 302호" 같은 접미사에서 자주 실패하기 때문.
String _trimAddress(String address) {
  var s = address.trim();
  s = s.replaceAll(RegExp(r'\([^)]*\)'), ' ');
  s = s.replaceAll(
    RegExp(r'\s*(지하\s*)?[BbＢ]?\d+\s*층.*$'),
    '',
  );
  s = s.replaceAll(RegExp(r'\s*\d+\s*호.*$'), '');
  return s.replaceAll(RegExp(r'\s+'), ' ').trim();
}

/// 기기 내장 지오코더로 가게 주소를 좌표로 바꾼다. 실패하면 null.
Future<({double lat, double lng})?> _geocode(String name, String address) async {
  final queries = <String>{
    _trimAddress(address),
    address.trim(),
    '$name ${_trimAddress(address)}'.trim(),
  }..removeWhere((q) => q.isEmpty);

  try {
    await setLocaleIdentifier('ko_KR');
  } catch (_) {
    // iOS 등 일부 플랫폼은 미구현. 무시하고 진행.
  }

  for (final query in queries) {
    try {
      final results = await locationFromAddress(query);
      if (results.isNotEmpty) {
        return (lat: results.first.latitude, lng: results.first.longitude);
      }
    } catch (e) {
      debugPrint('[directions] geocode 실패 ("$query"): $e');
    }
  }
  return null;
}

Uri _naverUri(
  DirectionsMode mode,
  String name, {
  ({double lat, double lng})? dest,
}) {
  if (dest == null) {
    return Uri.parse('nmap://search?query=${Uri.encodeComponent(name)}&appname=$_appId');
  }
  final path = switch (mode) {
    DirectionsMode.walk => 'walk',
    DirectionsMode.transit => 'public',
    DirectionsMode.car => 'car',
  };
  return Uri.parse(
    'nmap://route/$path?dlat=${dest.lat}&dlng=${dest.lng}'
    '&dname=${Uri.encodeComponent(name)}&appname=$_appId',
  );
}

Uri _kakaoUri(
  DirectionsMode mode,
  String name, {
  ({double lat, double lng})? dest,
}) {
  if (dest == null) {
    return Uri.parse('kakaomap://search?q=${Uri.encodeComponent(name)}');
  }
  final by = switch (mode) {
    DirectionsMode.walk => 'FOOT',
    DirectionsMode.transit => 'PUBLICTRANSIT',
    DirectionsMode.car => 'CAR',
  };
  return Uri.parse('kakaomap://route?ep=${dest.lat},${dest.lng}&by=$by');
}

Uri _googleUri(
  DirectionsMode mode,
  String name,
  String address, {
  ({double lat, double lng})? dest,
}) {
  final travelMode = switch (mode) {
    DirectionsMode.walk => 'walking',
    DirectionsMode.transit => 'transit',
    DirectionsMode.car => 'driving',
  };
  final destination = dest != null
      ? '${dest.lat},${dest.lng}'
      : '$name $address';
  return Uri.parse(
    'https://www.google.com/maps/dir/?api=1'
    '&destination=${Uri.encodeComponent(destination)}'
    '&travelmode=$travelMode',
  );
}

/// 해당 지도 앱이 기기에 설치되어 있는지(구글은 웹 폴백이 있어 항상 true).
Future<bool> isMapAppAvailable(MapApp app) async {
  final probe = switch (app) {
    MapApp.naver => Uri.parse('nmap://search?query=%20&appname=$_appId'),
    MapApp.kakao => Uri.parse('kakaomap://search?q=%20'),
    MapApp.google => null,
  };
  if (probe == null) return true;
  try {
    return await canLaunchUrl(probe);
  } catch (_) {
    return false;
  }
}

/// 현재 위치에서 가게까지의 길찾기를 지도 앱으로 연다.
///
/// 출발지는 넘기지 않으므로 지도 앱이 사용자의 현재 위치를 출발지로 잡는다.
/// 좌표가 없으면 앱 내장 지오코더로 변환을 시도하고, 그마저 실패하면
/// 네이버·카카오는 가게명 검색, 구글은 주소 텍스트로 연다.
///
/// 지도 앱을 실제로 열었으면 true.
Future<bool> openDirections({
  required MapApp app,
  required DirectionsMode mode,
  required String storeName,
  required String storeAddress,
  double? latitude,
  double? longitude,
}) async {
  final dest = latitude != null && longitude != null
      ? (lat: latitude, lng: longitude)
      : await _geocode(storeName, storeAddress);

  final primary = switch (app) {
    MapApp.naver => _naverUri(mode, storeName, dest: dest),
    MapApp.kakao => _kakaoUri(mode, storeName, dest: dest),
    MapApp.google => _googleUri(mode, storeName, storeAddress, dest: dest),
  };

  final mode0 = app == MapApp.google
      ? LaunchMode.externalApplication
      : LaunchMode.externalNonBrowserApplication;

  try {
    if (await launchUrl(primary, mode: mode0)) return true;
  } catch (e) {
    debugPrint('[directions] $app 딥링크 실패: $e');
  }

  // 네이버·카카오 앱 실행 실패 시 구글 지도 웹으로 폴백.
  if (app != MapApp.google) {
    try {
      return await launchUrl(
        _googleUri(mode, storeName, storeAddress, dest: dest),
        mode: LaunchMode.externalApplication,
      );
    } catch (e) {
      debugPrint('[directions] 구글 폴백 실패: $e');
    }
  }
  return false;
}
