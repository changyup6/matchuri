import 'package:flutter/material.dart';
import 'package:matchuri/models/app_user.dart';
import 'package:matchuri/services/user_api.dart';
import 'package:matchuri/widgets/primary_button.dart';

class OnboardingScreen extends StatefulWidget {
  final UserApi userService;
  final Future<void> Function(AppUser) onRegistered;

  const OnboardingScreen({
    super.key,
    required this.userService,
    required this.onRegistered,
  });

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  final TextEditingController _nameController = TextEditingController();
  bool _submitting = false;
  String? _error;

  @override
  void dispose() {
    _nameController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final name = _nameController.text.trim();
    if (name.isEmpty) {
      setState(() => _error = '이름을 입력해 주세요.');
      return;
    }

    setState(() {
      _submitting = true;
      _error = null;
    });

    try {
      final user = await widget.userService.register(name);
      if (!mounted) return;
      await widget.onRegistered(user);
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _submitting = false;
        _error = '등록에 실패했어요. 잠시 후 다시 시도해 주세요.';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      body: SafeArea(
        child: LayoutBuilder(
          builder: (context, constraints) => SingleChildScrollView(
            padding: const EdgeInsets.symmetric(horizontal: 24),
            child: ConstrainedBox(
              constraints: BoxConstraints(minHeight: constraints.maxHeight),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const Text(
                    '어떻게 불러드릴까요?',
                    style: TextStyle(
                      fontFamily: 'Paperlogy',
                      fontWeight: FontWeight.w800,
                      fontSize: 24,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    '입력한 이름으로 취향에 맞는 맛집을 추천해 드려요.',
                    style: TextStyle(
                      fontFamily: 'Paperlogy',
                      fontWeight: FontWeight.w500,
                      fontSize: 14,
                      color: Colors.grey.shade600,
                    ),
                  ),
                  const SizedBox(height: 32),
                  TextField(
                    controller: _nameController,
                    enabled: !_submitting,
                    textInputAction: TextInputAction.done,
                    onSubmitted: (_) => _submit(),
                    decoration: InputDecoration(
                      hintText: '이름',
                      errorText: _error,
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(13),
                      ),
                      focusedBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(13),
                        borderSide: const BorderSide(color: Color(0xffF59E41)),
                      ),
                    ),
                  ),
                  const SizedBox(height: 20),
                  PrimaryButton(
                    label: '시작하기',
                    onPressed: _submitting ? null : _submit,
                    isLoading: _submitting,
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
