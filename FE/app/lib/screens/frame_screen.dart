import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:matchuri/models/app_user.dart';
import 'package:matchuri/screens/my_screen.dart';
import 'package:matchuri/services/bookmark_api.dart';
import 'package:matchuri/screens/home_screen.dart';
import 'package:matchuri/screens/mark_screen.dart';
import 'package:matchuri/services/restaurant_api.dart';
import 'package:matchuri/services/restaurant_detail_api.dart';
import 'package:matchuri/services/preferences_api.dart';
import 'package:matchuri/services/taste_api.dart';
import 'package:matchuri/services/user_api.dart';

class FrameScreen extends StatefulWidget {
  final AppUser user;
  final PreferencesApi preferencesApi;
  final UserApi userApi;

  const FrameScreen({
    super.key,
    required this.user,
    required this.preferencesApi,
    required this.userApi,
  });

  @override
  State<FrameScreen> createState() => _FrameScreenState();
}

class _FrameScreenState extends State<FrameScreen> {
  late AppUser _user;
  late RestaurantApi _api;
  late final BookmarkApi _bookmarkApi;
  late RestaurantDetailApi _detailApi;
  late TasteApi _tasteApi;
  final ValueNotifier<int> _bookmarkVersion = ValueNotifier(0);
  final ValueNotifier<int> _myRefreshVersion = ValueNotifier(0);
  late List<Widget> _screens;
  int _selectedIndex = 0;

  @override
  void initState() {
    super.initState();
    _user = widget.user;
    _bookmarkApi = BookmarkApi(userId: _user.userId);
    _configureSession();
  }

  void _configureSession() {
    _api = RestaurantApi(userId: _user.userId, sessionId: _user.sessionId!);
    _detailApi = RestaurantDetailApi(
      userId: _user.userId,
      sessionId: _user.sessionId!,
    );
    _tasteApi = TasteApi();
    _screens = [
      HomeScreen(
        key: ValueKey(_user.sessionId),
        api: _api,
        bookmarkApi: _bookmarkApi,
        detailApi: _detailApi,
        userId: _user.userId,
        userName: _user.name,
        preferencesApi: widget.preferencesApi,
        initialFilter: _user.filter,
        onBookmarkChanged: () => _bookmarkVersion.value++,
        onTasteChanged: () => _myRefreshVersion.value++,
        onStartNewSession: _startNewSession,
      ),
      MarkScreen(
        api: _bookmarkApi,
        detailApi: _detailApi,
        bookmarkVersion: _bookmarkVersion,
      ),
      MyScreen(
        user: _user,
        tasteApi: _tasteApi,
        detailApi: _detailApi,
        bookmarkApi: _bookmarkApi,
        refreshVersion: _myRefreshVersion,
        onBookmarkChanged: () => _bookmarkVersion.value++,
      ),
    ];
  }

  Future<void> _startNewSession() async {
    final filter = _user.filter;
    final sessionId = await widget.userApi.createSession(_user.userId);
    if (!mounted) return;
    if (filter != null) {
      await widget.preferencesApi.saveFilters(
        userId: _user.userId,
        sessionId: sessionId,
        categories: filter.categories,
        tags: filter.tags,
        withinMeters: filter.withinMeters,
      );
    }
    if (!mounted) return;
    _api.dispose();
    _detailApi.dispose();
    _tasteApi.dispose();
    setState(() {
      _user = AppUser(
        userId: _user.userId,
        name: _user.name,
        createdAt: _user.createdAt,
        filter: _user.filter,
        sessionId: sessionId,
      );
      _selectedIndex = 0;
      _configureSession();
    });
  }

  @override
  void dispose() {
    _api.dispose();
    _bookmarkApi.dispose();
    _detailApi.dispose();
    _tasteApi.dispose();
    _bookmarkVersion.dispose();
    _myRefreshVersion.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    body: IndexedStack(index: _selectedIndex, children: _screens),
    bottomNavigationBar: BottomNavigationBar(
      currentIndex: _selectedIndex,
      onTap: (index) {
        if (index == 2) _myRefreshVersion.value++;
        setState(() => _selectedIndex = index);
      },
      selectedItemColor: Colors.black,
      unselectedItemColor: Colors.grey,
      selectedLabelStyle: const TextStyle(
        fontFamily: 'Paperlogy',
        color: Colors.black,
        fontSize: 13,
        fontWeight: FontWeight.w700,
      ),
      unselectedLabelStyle: const TextStyle(
        fontFamily: 'Paperlogy',
        color: Colors.grey,
        fontSize: 13,
        fontWeight: FontWeight.w700,
      ),
      backgroundColor: Colors.white,
      items: [
        BottomNavigationBarItem(
          label: '추천',
          icon: SvgPicture.asset('assets/icons/recommend.svg'),
          activeIcon: SvgPicture.asset('assets/icons/recommend_color.svg'),
        ),
        BottomNavigationBarItem(
          label: '담아둔 맛집',
          icon: SvgPicture.asset('assets/icons/bookmark.svg'),
          activeIcon: SvgPicture.asset('assets/icons/bookmark_color.svg'),
        ),
        BottomNavigationBarItem(
          label: '내 취향',
          icon: SvgPicture.asset('assets/icons/profile.svg'),
          activeIcon: SvgPicture.asset('assets/icons/profile_color.svg'),
        ),
      ],
    ),
  );
}
