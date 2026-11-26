import 'package:matchuri/models/app_user.dart';
import 'package:shared_preferences/shared_preferences.dart';

class UserService {
  static const _keyUserId = 'user_id';
  static const _keyUserName = 'user_name';
  static const _keyPreferencesCompleted = 'preferences_completed';
  static const _keyTutorialCompleted = 'tutorial_completed';
  Future<AppUser?> loadUser() async {
    final prefs = await SharedPreferences.getInstance();
    final userId = prefs.getString(_keyUserId);
    return userId == null
        ? null
        : AppUser(userId: userId, name: prefs.getString(_keyUserName) ?? '');
  }

  Future<void> saveUser(AppUser user) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_keyUserId, user.userId);
    await prefs.setString(_keyUserName, user.name);
    await prefs.setBool(_keyPreferencesCompleted, false);
  }

  Future<void> setPreferencesIncomplete() async =>
      (await SharedPreferences.getInstance()).setBool(
        _keyPreferencesCompleted,
        false,
      );
  Future<bool> loadPreferencesCompleted() async =>
      (await SharedPreferences.getInstance()).getBool(
        _keyPreferencesCompleted,
      ) ??
      false;
  Future<void> setPreferencesCompleted() async =>
      (await SharedPreferences.getInstance()).setBool(
        _keyPreferencesCompleted,
        true,
      );
  Future<bool> loadTutorialCompleted() async =>
      (await SharedPreferences.getInstance()).getBool(_keyTutorialCompleted) ??
      false;
  Future<void> setTutorialCompleted() async =>
      (await SharedPreferences.getInstance()).setBool(
        _keyTutorialCompleted,
        true,
      );
}
