import 'package:flutter/material.dart';
import 'package:matchuri/services/directions_launcher.dart';
import 'package:matchuri/services/visit_geofence_service.dart';
import 'package:matchuri/services/survey_api.dart';
import 'package:matchuri/models/visit_review.dart';
import 'package:matchuri/screens/visit_review_screen.dart';

class DirectionsButton extends StatelessWidget {
  final String userId;
  final int cardId;
  final String storeName;
  final String storeAddress;
  final double? latitude;
  final double? longitude;

  const DirectionsButton({
    super.key,
    required this.userId,
    required this.cardId,
    required this.storeName,
    required this.storeAddress,
    this.latitude,
    this.longitude,
  });

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.centerLeft,
      child: OutlinedButton.icon(
        onPressed: () => _openSheet(context),
        icon: const Icon(Icons.directions_outlined, size: 18),
        label: const Text(
          '길찾기',
          style: TextStyle(
            fontFamily: 'Paperlogy',
            fontWeight: FontWeight.w600,
            fontSize: 14,
          ),
        ),
        style: OutlinedButton.styleFrom(
          foregroundColor: const Color(0xffB4650F),
          side: const BorderSide(color: Color(0xffF59E41)),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(999),
          ),
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
        ),
      ),
    );
  }

  void _openSheet(BuildContext context) {
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: Colors.white,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => _DirectionsSheet(
        userId: userId,
        cardId: cardId,
        storeName: storeName,
        storeAddress: storeAddress,
        latitude: latitude,
        longitude: longitude,
      ),
    );
  }
}

class _DirectionsSheet extends StatefulWidget {
  final String userId;
  final int cardId;
  final String storeName;
  final String storeAddress;
  final double? latitude;
  final double? longitude;

  const _DirectionsSheet({
    required this.userId,
    required this.cardId,
    required this.storeName,
    required this.storeAddress,
    this.latitude,
    this.longitude,
  });

  @override
  State<_DirectionsSheet> createState() => _DirectionsSheetState();
}

class _DirectionsSheetState extends State<_DirectionsSheet> {
  DirectionsMode _mode = DirectionsMode.walk;
  late final Future<List<MapApp>> _appsFuture = _loadApps();
  bool _launching = false;

  Future<List<MapApp>> _loadApps() async {
    final available = <MapApp>[];
    for (final app in MapApp.values) {
      if (await isMapAppAvailable(app)) available.add(app);
    }
    return available;
  }

  Future<void> _launch(MapApp app) async {
    if (_launching) return;
    setState(() => _launching = true);
    final api = SurveyApi();
    try {
      await api.recordDirection(userId: widget.userId, cardId: widget.cardId,
          mapApp: app.name, mode: _mode.name);
      if (!mounted) return;
      final status = await VisitGeofenceService.register(
        userId: widget.userId, cardId: widget.cardId,
        storeName: widget.storeName, latitude: widget.latitude, longitude: widget.longitude,
      );
      if (!mounted) return;
      final ok = await openDirections(app: app, mode: _mode,
          storeName: widget.storeName, storeAddress: widget.storeAddress,
          latitude: widget.latitude, longitude: widget.longitude);
      if (!mounted) return;
      final messenger = ScaffoldMessenger.of(context);
      final navigator = Navigator.of(context);
      navigator.pop();
      if (!ok) messenger.showSnackBar(const SnackBar(content: Text('지도 앱을 열지 못했어요.')));
      if (status != 'registered') {
        messenger.showSnackBar(SnackBar(
          content: Text(switch (status) {
            'notifications' => '알림을 허용하면 도착·리뷰 알림을 받을 수 있어요.',
            'fineLocation' => '도착 알림을 위해 정확한 위치 권한을 허용해 주세요.',
            'backgroundLocation' => '도착 알림을 위해 위치 권한을 항상 허용으로 설정해 주세요.',
            'coordinatesMissing' => '식당 좌표가 없어 도착 알림을 등록하지 못했어요.',
            'unsupported' => '현재 환경에서는 Android 방문 알림을 사용할 수 없어요.',
            _ => '방문 알림을 등록하지 못했어요. 알림 설정을 확인해 주세요.',
          }),
          action: SnackBarAction(label: '설정', onPressed: () async {
            try { await VisitGeofenceService.openSettings(); } catch (_) {}
          }),
        ));
      }
      // Notification denial must not prevent testing the actual review form.
      messenger.showSnackBar(SnackBar(
        content: Text(VisitGeofenceService.simulateArrival
            ? '테스트 도착·리뷰 알림을 요청했어요.' : '도착 후 방문 이야기를 남겨주세요.'),
        action: SnackBarAction(label: '리뷰 쓰기', onPressed: () {
          messenger.hideCurrentSnackBar();
          navigator.push(MaterialPageRoute<void>(builder: (_) => VisitReviewScreen(
            target: VisitReviewTarget(userId: widget.userId, cardId: widget.cardId,
              storeName: widget.storeName, visitId: 'manual', simulated: VisitGeofenceService.simulateArrival),
          )));
        }),
      ));
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(
          error is SurveyApiException ? error.toString() : '길찾기 기록을 저장하지 못했어요. 다시 시도해 주세요.',
        )));
      }
    } finally {
      api.dispose();
      if (mounted) setState(() => _launching = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 16, 20, 20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              '길찾기',
              style: TextStyle(
                fontFamily: 'Paperlogy',
                fontWeight: FontWeight.w800,
                fontSize: 18,
              ),
            ),
            const SizedBox(height: 4),
            Text(
              '현재 위치에서 ${widget.storeName}까지',
              style: TextStyle(
                fontFamily: 'Paperlogy',
                fontWeight: FontWeight.w500,
                fontSize: 13,
                color: Colors.grey.shade600,
              ),
            ),
            const SizedBox(height: 16),
            Wrap(
              spacing: 8,
              children: DirectionsMode.values.map((mode) {
                final selected = mode == _mode;
                return ChoiceChip(
                  label: Text(mode.label),
                  selected: selected,
                  onSelected: (_) => setState(() => _mode = mode),
                  labelStyle: TextStyle(
                    fontFamily: 'Paperlogy',
                    fontWeight: FontWeight.w600,
                    fontSize: 13,
                    color: selected ? Colors.white : Colors.grey.shade700,
                  ),
                  showCheckmark: false,
                  selectedColor: const Color(0xffF59E41),
                  backgroundColor: const Color(0xffF3F4F6),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(999),
                  ),
                  side: BorderSide.none,
                );
              }).toList(),
            ),
            const SizedBox(height: 16),
            FutureBuilder<List<MapApp>>(
              future: _appsFuture,
              builder: (context, snapshot) {
                if (!snapshot.hasData) {
                  return const Padding(
                    padding: EdgeInsets.symmetric(vertical: 24),
                    child: Center(child: CircularProgressIndicator()),
                  );
                }
                final apps = snapshot.data!;
                return Column(
                  children: apps
                      .map(
                        (app) => ListTile(
                          contentPadding: EdgeInsets.zero,
                          leading: const Icon(
                            Icons.map_outlined,
                            color: Color(0xffB4650F),
                          ),
                          title: Text(
                            app.label,
                            style: const TextStyle(
                              fontFamily: 'Paperlogy',
                              fontWeight: FontWeight.w600,
                              fontSize: 15,
                            ),
                          ),
                          trailing: const Icon(Icons.chevron_right),
                          enabled: !_launching,
                          onTap: () => _launch(app),
                        ),
                      )
                      .toList(),
                );
              },
            ),
          ],
        ),
      ),
    );
  }
}
