import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:matchuri/models/app_user.dart';
import 'package:matchuri/screens/filter_screen.dart';
import 'package:matchuri/screens/frame_screen.dart';
import 'package:matchuri/screens/onboarding_screen.dart';
import 'package:matchuri/screens/tutorial_screen.dart';
import 'package:matchuri/services/user_service.dart';
import 'package:matchuri/services/user_api.dart';
import 'package:matchuri/services/preferences_api.dart';

class BootstrapGate extends StatefulWidget {
  const BootstrapGate({super.key});

  @override
  State<BootstrapGate> createState() => _BootstrapGateState();
}

class _BootstrapGateState extends State<BootstrapGate> {
  final UserService _userService = UserService();
  final UserApi _userApi = UserApi();
  final PreferencesApi _preferencesApi = PreferencesApi();
  AppUser? _user;
  bool _hasCompletedPreferences = false;
  bool _hasCompletedTutorial = false;
  bool _loading = true;
  String? _loadError;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    _loadError = null;
    var user = await _userService.loadUser();
    if (user != null) {
      try {
        user = await _startSession(user);
        await _userService.saveUser(user);
        await _userService.setPreferencesIncomplete();
      } catch (_) {
        _loadError = '추천 세션을 시작하지 못했어요. 잠시 후 다시 시도해 주세요.';
      }
    }
    final hasCompletedPreferences = await _userService
        .loadPreferencesCompleted();
    final hasCompletedTutorial = await _userService.loadTutorialCompleted();
    if (!mounted) return;
    setState(() {
      _user = user;
      _hasCompletedPreferences = hasCompletedPreferences;
      _hasCompletedTutorial = hasCompletedTutorial;
      _loading = false;
    });
  }

  Future<AppUser> _startSession(AppUser user) async {
    AppUser person;
    try {
      person = await _userApi.fetchUser(user.userId);
    } catch (_) {
      final userId = await _userApi.lookupLegacySessionUserId(user.userId);
      person = await _userApi.fetchUser(userId);
    }
    return person.copyWith(
      sessionId: await _userApi.createSession(person.userId),
    );
  }

  @override
  void dispose() {
    _userApi.dispose();
    _preferencesApi.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return Scaffold(
        backgroundColor: const Color(0xffF59E41),
        body: Center(
          child: SvgPicture.asset(
            'assets/icons/not network_animation.svg',
            width: 168,
            height: 168,
          ),
        ),
      );
    }

    final user = _user;
    if (_loadError != null) {
      return Scaffold(
        body: Center(
          child: TextButton(onPressed: _load, child: Text(_loadError!)),
        ),
      );
    }
    if (!_hasCompletedTutorial) {
      return TutorialScreen(
        onCompleted: () async {
          await _userService.setTutorialCompleted();
          if (mounted) setState(() => _hasCompletedTutorial = true);
        },
      );
    }
    if (user == null) {
      return OnboardingScreen(
        userService: _userApi,
        onRegistered: (registered) async {
          final user = await _startSession(registered);
          await _userService.saveUser(user);
          if (!mounted) return;
          setState(() {
            _user = user;
            _hasCompletedPreferences = false;
          });
        },
      );
    }
    if (!_hasCompletedPreferences) {
      return FilterScreen(
        userId: user.userId,
        sessionId: user.sessionId!,
        userService: _preferencesApi,
        onCompleted: (filter) async {
          await _userService.setPreferencesCompleted();
          if (mounted) {
            setState(() {
              _user = user.copyWith(filter: filter);
              _hasCompletedPreferences = true;
            });
          }
        },
      );
    }
    return FrameScreen(
      user: user,
      preferencesApi: _preferencesApi,
      userApi: _userApi,
    );
  }
}
