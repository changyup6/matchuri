import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:matchuri/models/restaurant_card.dart';
import 'package:matchuri/screens/restaurant_detail_screen.dart';
import 'package:matchuri/services/bookmark_api.dart';
import 'package:matchuri/services/restaurant_detail_api.dart';
import 'package:matchuri/widgets/restaurant_list_card.dart';

const _categoryIcons = {
  '한식': 'korean',
  '중식': 'chinese',
  '일식': 'japan',
  '양식': 'western',
  '분식': 'bunsik',
  '카페': 'cafe',
  '술집': 'alcohol',
  '기타': 'etc',
  '간식': 'dessert',
};

class MarkScreen extends StatefulWidget {
  final BookmarkApi api;
  final RestaurantDetailApi detailApi;
  final ValueListenable<int> bookmarkVersion;

  const MarkScreen({
    super.key,
    required this.api,
    required this.detailApi,
    required this.bookmarkVersion,
  });

  @override
  State<MarkScreen> createState() => _MarkScreenState();
}

class _MarkScreenState extends State<MarkScreen> {
  late Future<List<RestaurantCard>> _future;
  String? _selectedCategory;

  @override
  void initState() {
    super.initState();
    _future = widget.api.fetchBookmarks();
    widget.bookmarkVersion.addListener(_refreshOnBookmarkChange);
  }

  void _refreshOnBookmarkChange() async {
    try {
      await _refresh();
    } catch (error) {
      debugPrint('Bookmark refresh after addition failed: $error');
    }
  }

  Future<void> _refresh() async {
    final cards = await widget.api.fetchBookmarks();
    if (mounted) {
      setState(() {
        _future = Future.value(cards);
      });
    }
  }

  Future<void> _refreshAfterRemoval(int cardId) async {
    // ponytail: retry briefly for eventual consistency; use server push if this becomes common.
    for (var attempt = 0; attempt < 3; attempt++) {
      try {
        final cards = await widget.api.fetchBookmarks();
        if (cards.every((card) => card.cardId != cardId)) {
          if (mounted) {
            setState(() {
              _future = Future.value(cards);
            });
          }
          return;
        }
      } catch (error) {
        debugPrint('Bookmark refresh after removal failed: $error');
      }
      await Future<void>.delayed(const Duration(milliseconds: 300));
    }
  }

  Future<void> _removeBookmark(int cardId) async {
    try {
      await widget.api.unbookmark(cardId);
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('찜 해제에 실패했어요. 다시 시도해 주세요.')),
        );
      }
      return;
    }

    final currentFuture = _future;
    setState(() {
      _future = currentFuture.then(
        (cards) => cards.where((card) => card.cardId != cardId).toList(),
      );
    });
    if (mounted) {
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('찜 목록에서 삭제했어요.')));
    }
    await _refreshAfterRemoval(cardId);
  }

  Future<void> _confirmRemoveBookmark(RestaurantCard card) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        backgroundColor: Colors.white,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        title: const Row(
          children: [
            Icon(Icons.bookmark_remove_rounded, color: Color(0xffF59E41)),
            SizedBox(width: 8),
            Flexible(
              child: Text(
                '찜을 삭제할까요?',
                style: TextStyle(
                  fontFamily: 'Paperlogy',
                  fontWeight: FontWeight.w800,
                ),
              ),
            ),
          ],
        ),
        content: Text(
          '${card.store.name}을(를) 찜 목록에서 삭제합니다.\n정말 삭제하시겠습니까?',
          style: const TextStyle(fontFamily: 'Paperlogy', height: 1.5),
        ),
        actionsPadding: const EdgeInsets.fromLTRB(20, 0, 20, 16),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            style: TextButton.styleFrom(foregroundColor: Colors.black),
            child: const Text(
              '취소',
              style: TextStyle(
                fontFamily: 'Paperlogy',
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            style: FilledButton.styleFrom(
              backgroundColor: const Color(0xffF59E41),
              foregroundColor: Colors.white,
            ),
            child: const Text(
              '삭제',
              style: TextStyle(
                fontFamily: 'Paperlogy',
                fontWeight: FontWeight.w700,
              ),
            ),
          ),
        ],
      ),
    );
    if (confirmed == true && mounted) await _removeBookmark(card.cardId);
  }

  void _openDetail(RestaurantCard card) {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => RestaurantDetailScreen(
          card: card,
          api: widget.detailApi,
          bookmarkApi: widget.api,
          initialBookmarked: true,
          onBookmarkChanged: (bookmarked) {
            if (bookmarked) {
              _refreshOnBookmarkChange();
            } else {
              final currentFuture = _future;
              setState(() {
                _future = currentFuture.then(
                  (cards) => cards
                      .where((item) => item.cardId != card.cardId)
                      .toList(),
                );
              });
            }
          },
        ),
      ),
    );
  }

  String _categoryOf(RestaurantCard card) =>
      _categoryIcons.containsKey(card.store.categoryGroup)
      ? card.store.categoryGroup!
      : '기타';

  @override
  void dispose() {
    widget.bookmarkVersion.removeListener(_refreshOnBookmarkChange);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      body: SafeArea(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Padding(
              padding: EdgeInsets.fromLTRB(20, 20, 20, 12),
              child: Text(
                '담아둔 맛집',
                style: TextStyle(
                  fontFamily: 'Paperlogy',
                  fontWeight: FontWeight.w800,
                  fontSize: 22,
                ),
              ),
            ),
            Expanded(
              child: RefreshIndicator(
                onRefresh: _refresh,
                child: FutureBuilder<List<RestaurantCard>>(
                  future: _future,
                  builder: (context, snapshot) {
                    if (snapshot.connectionState != ConnectionState.done) {
                      return const Center(child: CircularProgressIndicator());
                    }

                    if (snapshot.hasError) {
                      return _MessageView(
                        message: '담아둔 맛집을 불러오지 못했습니다.',
                        onRetry: _refresh,
                      );
                    }

                    final cards = snapshot.data ?? const [];
                    if (cards.isEmpty) {
                      return const _MessageView(
                        message: '아직 담아둔 맛집이 없어요.\n마음에 드는 식당을 찜하고 확인해보세요.',
                      );
                    }

                    final selectedCategory =
                        _selectedCategory ??
                        _categoryIcons.keys.firstWhere(
                          (category) => cards.any(
                            (card) => _categoryOf(card) == category,
                          ),
                          orElse: () => _categoryIcons.keys.first,
                        );
                    final visibleCards = cards
                        .where((card) => _categoryOf(card) == selectedCategory)
                        .toList();

                    return ListView(
                      padding: const EdgeInsets.fromLTRB(20, 4, 20, 24),
                      children: [
                        _CategoryGrid(
                          selected: selectedCategory,
                          onSelected: (category) =>
                              setState(() => _selectedCategory = category),
                        ),
                        const SizedBox(height: 20),
                        const Divider(color: Color(0xffE5E7EB)),
                        const SizedBox(height: 12),
                        Text(
                          '총 ${visibleCards.length}건',
                          style: const TextStyle(
                            fontFamily: 'Paperlogy',
                            fontWeight: FontWeight.w700,
                            fontSize: 18,
                          ),
                        ),
                        const SizedBox(height: 14),
                        if (visibleCards.isEmpty)
                          const Padding(
                            padding: EdgeInsets.symmetric(vertical: 48),
                            child: Text(
                              '이 카테고리에 담아둔 맛집이 없어요.',
                              textAlign: TextAlign.center,
                              style: TextStyle(
                                color: Color(0xff9CA3AF),
                                fontFamily: 'Paperlogy',
                                fontSize: 14,
                              ),
                            ),
                          )
                        else
                          for (final card in visibleCards)
                            RestaurantListCard(
                              card: card,
                              onTap: () => _openDetail(card),
                              onRemove: () => _confirmRemoveBookmark(card),
                            ),
                      ],
                    );
                  },
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _CategoryGrid extends StatelessWidget {
  final String selected;
  final ValueChanged<String> onSelected;

  const _CategoryGrid({required this.selected, required this.onSelected});

  @override
  Widget build(BuildContext context) => GridView.count(
    crossAxisCount: 3,
    crossAxisSpacing: 10,
    mainAxisSpacing: 10,
    mainAxisExtent: 52,
    shrinkWrap: true,
    physics: const NeverScrollableScrollPhysics(),
    children: [
      for (final entry in _categoryIcons.entries)
        _CategoryButton(
          label: entry.key,
          icon: entry.value,
          selected: selected == entry.key,
          onTap: () => onSelected(entry.key),
        ),
    ],
  );
}

class _CategoryButton extends StatelessWidget {
  final String label;
  final String icon;
  final bool selected;
  final VoidCallback onTap;

  const _CategoryButton({
    required this.label,
    required this.icon,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) => Semantics(
    button: true,
    selected: selected,
    label: label,
    child: InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(14),
      child: Ink(
        decoration: BoxDecoration(
          color: selected ? const Color(0xffFFF7EE) : const Color(0xffF7F8FA),
          border: Border.all(
            color: selected ? const Color(0xffFFD2A3) : const Color(0xffD9DCE2),
            width: 1.5,
          ),
          borderRadius: BorderRadius.circular(14),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Image.asset(
              'assets/icons/$icon${selected ? '_color' : ''}.png',
              width: 21,
              height: 21,
            ),
            const SizedBox(width: 6),
            Flexible(
              child: Text(
                label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  color: selected
                      ? const Color(0xffF59E41)
                      : const Color(0xff9CA3AF),
                  fontFamily: 'Paperlogy',
                  fontWeight: FontWeight.w700,
                  fontSize: 14,
                ),
              ),
            ),
          ],
        ),
      ),
    ),
  );
}

class _MessageView extends StatelessWidget {
  final String message;
  final Future<void> Function()? onRetry;

  const _MessageView({required this.message, this.onRetry});

  @override
  Widget build(BuildContext context) {
    return ListView(
      children: [
        SizedBox(height: MediaQuery.sizeOf(context).height * 0.25),
        Center(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 32),
            child: Column(
              children: [
                Text(
                  message,
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    fontFamily: 'Paperlogy',
                    fontWeight: FontWeight.w500,
                    fontSize: 14,
                    color: Colors.grey.shade600,
                    height: 1.5,
                  ),
                ),
                if (onRetry != null) ...[
                  const SizedBox(height: 16),
                  TextButton(onPressed: onRetry, child: const Text('다시 시도')),
                ],
              ],
            ),
          ),
        ),
      ],
    );
  }
}
