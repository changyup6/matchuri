import 'package:flutter/material.dart';
import 'package:matchuri/screens/bootstrap_gate.dart';
import 'package:matchuri/screens/visit_review_screen.dart';
import 'package:matchuri/services/visit_geofence_service.dart';
import 'package:matchuri/services/user_service.dart';

class MatchuriApp extends StatefulWidget {
  const MatchuriApp({super.key});

  @override
  State<MatchuriApp> createState() => _MatchuriAppState();
}

class _MatchuriAppState extends State<MatchuriApp> with WidgetsBindingObserver {
  final _navigator = GlobalKey<NavigatorState>();
  final _messenger = GlobalKey<ScaffoldMessengerState>();
  final _opened = <String>{};
  bool _consuming = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    VisitGeofenceService.onReviewAvailable(_openPendingReview);
    WidgetsBinding.instance.addPostFrameCallback((_) => _openPendingReview());
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _openPendingReview();
  }

  Future<void> _openPendingReview() async {
    if (_consuming || !mounted || _navigator.currentState == null) return;
    _consuming = true;
    try {
      final target = await VisitGeofenceService.consumeReviewLaunch();
      if (target == null || !mounted || _opened.contains(target.visitId)) return;
      final user = await UserService().loadUser();
      if (!mounted) return;
      if (user?.userId != target.userId) {
        _messenger.currentState?.showSnackBar(const SnackBar(content: Text('이 알림의 사용자 정보가 현재 계정과 달라요.')));
        return;
      }
      _opened.add(target.visitId);
      _messenger.currentState?.removeCurrentSnackBar();
      _navigator.currentState!.push(MaterialPageRoute<void>(
        builder: (_) => VisitReviewScreen(target: target),
      )).whenComplete(() => _opened.remove(target.visitId));
    } catch (error) {
      debugPrint('리뷰 알림 열기 실패: $error');
    } finally {
      _consuming = false;
    }
  }

  @override
  void dispose() {
    VisitGeofenceService.onReviewAvailable(null);
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
        navigatorKey: _navigator,
        scaffoldMessengerKey: _messenger,
        home: BootstrapGate(),
        debugShowCheckedModeBanner: false,
      );
}
