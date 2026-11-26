import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:matchuri/widgets/primary_button.dart';

class TutorialScreen extends StatefulWidget {
  final Future<void> Function() onCompleted;

  const TutorialScreen({super.key, required this.onCompleted});

  @override
  State<TutorialScreen> createState() => _TutorialScreenState();
}

class _TutorialScreenState extends State<TutorialScreen> {
  final _controller = PageController();
  var _page = 0;
  var _finishing = false;

  Future<void> _complete() async {
    if (_finishing) return;
    setState(() => _finishing = true);
    await widget.onCompleted();
  }

  void _nextPage() {
    if (_page < _assets.length - 1) {
      _controller.nextPage(
        duration: const Duration(milliseconds: 250),
        curve: Curves.easeOut,
      );
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        backgroundColor: Colors.white,
        body: Stack(
          children: [
            GestureDetector(
              onTap: _nextPage,
              child: PageView.builder(
                controller: _controller,
                itemCount: _assets.length,
                onPageChanged: (page) => setState(() => _page = page),
                itemBuilder: (context, index) => _TutorialPage(
                  asset: _assets[index],
                  showSwipeHint: index == 1,
                  showClickHint: index == 2,
                ),
              ),
            ),
            Positioned(
              left: 16,
              right: 16,
              bottom: 12,
              child: SafeArea(
                top: false,
                child: _TutorialControls(
                  page: _page,
                  count: _assets.length,
                  onClose: _finishing ? null : _complete,
                ),
              ),
            ),
          ],
        ),
      );
}

class _TutorialControls extends StatelessWidget {
  final int page;
  final int count;
  final VoidCallback? onClose;

  const _TutorialControls({
    required this.page,
    required this.count,
    required this.onClose,
  });

  @override
  Widget build(BuildContext context) => Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: List.generate(
              count,
              (index) => AnimatedContainer(
                duration: const Duration(milliseconds: 180),
                width: index == page ? 16 : 6,
                height: 6,
                margin: const EdgeInsets.symmetric(horizontal: 3),
                decoration: BoxDecoration(
                  color: index == page
                      ? const Color(0xFF9C671E)
                      : const Color(0xFFD7D7D7),
                  borderRadius: BorderRadius.circular(99),
                ),
              ),
            ),
          ),
          if (page == count - 1)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: PrimaryButton(
                label: '튜토리얼 닫기',
                onPressed: onClose,
              ),
            ),
        ],
      );
}

const _assets = [
  'assets/sample/tutorial1.png',
  'assets/sample/tutorial2.png',
  'assets/sample/tutorial3.png',
  'assets/sample/tutorial4.png',
];

class _TutorialPage extends StatelessWidget {
  final String asset;
  final bool showSwipeHint;
  final bool showClickHint;

  const _TutorialPage({
    required this.asset,
    required this.showSwipeHint,
    required this.showClickHint,
  });

  @override
  Widget build(BuildContext context) => Stack(
        fit: StackFit.expand,
        children: [
          Image.asset(asset, fit: BoxFit.cover),
          if (showSwipeHint)
            const Align(
              alignment: Alignment(0, 0.15),
              child: _SwipeHint(),
            ),
          if (showClickHint)
            const Align(
              alignment: Alignment(0, 0.15),
              child: _ClickHint(),
            ),
        ],
      );
}

class _SwipeHint extends StatefulWidget {
  const _SwipeHint();

  @override
  State<_SwipeHint> createState() => _SwipeHintState();
}

class _ClickHint extends StatefulWidget {
  const _ClickHint();

  @override
  State<_ClickHint> createState() => _ClickHintState();
}

class _ClickHintState extends State<_ClickHint>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 800),
  )..repeat(reverse: true);

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ScaleTransition(
        scale: Tween(begin: .9, end: 1.1).animate(_controller),
        child: SvgPicture.asset(
          'assets/icons/click_no_watermark_flutter.svg',
          width: 120,
          height: 120,
        ),
      );
}

class _SwipeHintState extends State<_SwipeHint>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1800),
  )..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
        animation: _controller,
        builder: (context, _) {
          final left = _controller.value < .5;
          final progress = (left ? _controller.value : _controller.value - .5) * 2;
          return Transform.translate(
            offset: Offset((left ? -1 : 1) * 48 * progress, 0),
            child: SvgPicture.asset(
              left
                  ? 'assets/icons/swipe_no_watermark_flutter.svg'
                  : 'assets/icons/swipe_right_no_watermark_flutter.svg',
              width: 180,
              height: 180,
            ),
          );
        },
      );
}
