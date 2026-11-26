import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:matchuri/models/menu_item.dart';
import 'package:matchuri/models/restaurant_card.dart';
import 'package:matchuri/screens/restaurant_detail_screen.dart';
import 'package:matchuri/services/bookmark_api.dart';
import 'package:matchuri/services/restaurant_detail_api.dart';
import 'package:matchuri/services/original_card_images.dart';
import 'package:matchuri/widgets/menu_tile.dart';
import 'package:matchuri/widgets/original_photo_viewer.dart';

class CardWidget extends StatefulWidget {
  final RestaurantCard card;
  final BookmarkApi bookmarkApi;
  final RestaurantDetailApi detailApi;
  final int swipePercent;
  final bool initialBookmarked;
  final VoidCallback? onBookmarkChanged;

  const CardWidget({
    super.key,
    required this.card,
    required this.bookmarkApi,
    required this.detailApi,
    this.swipePercent = 0,
    this.initialBookmarked = false,
    this.onBookmarkChanged,
  });

  @override
  State<CardWidget> createState() => _CardWidgetState();
}

class _CardWidgetState extends State<CardWidget> {
  static const _animationDuration = Duration(milliseconds: 260);

  bool _expanded = false;
  bool _bookmarking = false;
  bool get _bookmarked => widget.bookmarkApi.isBookmarked(
    widget.card,
    fallback: widget.initialBookmarked,
  );
  int _cardVersion = 0;
  Future<List<MenuItem>?>? _menuFuture;
  late OriginalCardImages _originalImages;

  @override
  void initState() {
    super.initState();
    widget.bookmarkApi.addListener(_refreshBookmark);
    _originalImages = OriginalCardImages(
      api: widget.detailApi,
      cardId: widget.card.cardId,
    );
  }

  void _refreshBookmark() {
    if (mounted) setState(() {});
  }

  @override
  void didUpdateWidget(covariant CardWidget oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.bookmarkApi != widget.bookmarkApi) {
      oldWidget.bookmarkApi.removeListener(_refreshBookmark);
      widget.bookmarkApi.addListener(_refreshBookmark);
    }
    if (oldWidget.card.cardId != widget.card.cardId ||
        oldWidget.detailApi != widget.detailApi ||
        oldWidget.bookmarkApi != widget.bookmarkApi) {
      _cardVersion++;
      _bookmarking = false;
      _expanded = false;
      _menuFuture = null;
      _originalImages = OriginalCardImages(
        api: widget.detailApi,
        cardId: widget.card.cardId,
      );
    }
  }

  @override
  void dispose() {
    widget.bookmarkApi.removeListener(_refreshBookmark);
    super.dispose();
  }

  Future<List<MenuItem>?> _loadMenus() async {
    return widget.card.menus ??
        (await widget.detailApi.fetchCardDetail(widget.card.cardId)).menus;
  }

  void _toggleExpanded() {
    setState(() {
      _expanded = !_expanded;
      if (_expanded && _menuFuture == null) {
        _menuFuture = _loadMenus();
        // The request can fail before the expanding FutureBuilder is mounted.
        // Keep its error available to that builder without an unhandled gap.
        _menuFuture!.ignore();
      }
    });
  }

  Future<void> _bookmark() async {
    if (_bookmarking) return;
    final version = _cardVersion;
    final onChanged = widget.onBookmarkChanged;
    setState(() => _bookmarking = true);
    final nextBookmarked = !_bookmarked;
    try {
      if (nextBookmarked) {
        await widget.bookmarkApi.bookmark(widget.card.cardId);
      } else {
        await widget.bookmarkApi.unbookmark(widget.card.cardId);
      }
      if (mounted) onChanged?.call();
      if (!mounted || version != _cardVersion) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(nextBookmarked ? '찜 목록에 담았어요.' : '찜 목록에서 삭제했어요.'),
        ),
      );
    } catch (_) {
      if (!mounted || version != _cardVersion) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            nextBookmarked
                ? '찜하기에 실패했어요. 다시 시도해 주세요.'
                : '찜 해제에 실패했어요. 다시 시도해 주세요.',
          ),
        ),
      );
    } finally {
      if (mounted && version == _cardVersion) {
        setState(() => _bookmarking = false);
      }
    }
  }

  void _openDetail() {
    final onChanged = widget.onBookmarkChanged;
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => RestaurantDetailScreen(
          card: widget.card,
          api: widget.detailApi,
          bookmarkApi: widget.bookmarkApi,
          initialBookmarked: _bookmarked,
          onBookmarkChanged: (_) => onChanged?.call(),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final store = widget.card.store;
    final cacheWidth =
        (MediaQuery.sizeOf(context).width *
                MediaQuery.devicePixelRatioOf(context))
            .round();
    final likeOpacity = (widget.swipePercent / 100).clamp(0.0, 1.0);
    final nopeOpacity = (-widget.swipePercent / 100).clamp(0.0, 1.0);

    return Container(
      decoration: BoxDecoration(borderRadius: BorderRadius.circular(20)),
      clipBehavior: Clip.antiAlias,
      child: Stack(
        fit: StackFit.expand,
        children: [
          GestureDetector(
            onTap: _toggleExpanded,
            child: widget.card.image != null
                ? CachedNetworkImage(
                    imageUrl: widget.card.image!,
                    fit: BoxFit.cover,
                    memCacheWidth: cacheWidth,
                    fadeInDuration: const Duration(milliseconds: 150),
                    placeholder: (context, url) =>
                        Container(color: Colors.grey.shade200),
                    errorWidget: (context, url, error) =>
                        Container(color: Colors.grey.shade300),
                  )
                : Container(color: Colors.grey.shade300),
          ),
          Positioned(
            top: 28,
            left: 24,
            child: IgnorePointer(
              child: Opacity(
                opacity: likeOpacity,
                child: Transform.rotate(
                  angle: -0.3,
                  child: const _SwipeStamp(
                    text: '땡겨요',
                    color: Color(0xffF59E41),
                  ),
                ),
              ),
            ),
          ),
          Positioned(
            top: 28,
            right: 24,
            child: IgnorePointer(
              child: Opacity(
                opacity: nopeOpacity,
                child: Transform.rotate(
                  angle: 0.3,
                  child: const _SwipeStamp(
                    text: '안땡겨요',
                    color: Color(0xff6B7280),
                  ),
                ),
              ),
            ),
          ),
          Positioned(
            left: 0,
            right: 0,
            bottom: 0,
            child: AnimatedContainer(
              duration: _animationDuration,
              curve: Curves.easeOutCubic,
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.bottomCenter,
                  end: Alignment.topCenter,
                  colors: [
                    Colors.white.withValues(alpha: 0.9),
                    Colors.white.withValues(alpha: 0.9),
                    Colors.white.withValues(alpha: 0.9),
                  ],
                  stops: const [0.0, 0.55, 1.0],
                ),
              ),
              child: GestureDetector(
                onTap: _expanded ? null : _toggleExpanded,
                behavior: HitTestBehavior.translucent,
                child: AnimatedSize(
                  duration: _animationDuration,
                  curve: Curves.easeOutCubic,
                  alignment: Alignment.bottomCenter,
                  child: Padding(
                    padding: EdgeInsets.fromLTRB(
                      20,
                      _expanded ? 18 : 8,
                      20,
                      _expanded ? 16 : 20,
                    ),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          store.name,
                          style: TextStyle(
                            fontFamily: 'Paperlogy',
                            fontWeight: FontWeight.w800,
                            color: Colors.black,
                            fontSize: _expanded ? 20 : 26,
                          ),
                        ),
                        Text(
                          store.category,
                          style: TextStyle(
                            fontFamily: 'Paperlogy',
                            fontWeight: FontWeight.w500,
                            color: Color(0xff9CA3AF),
                            fontSize: _expanded ? 13 : 14,
                          ),
                        ),
                        if (widget.card.distanceMeters != null ||
                            store.tags.isNotEmpty) ...[
                          SizedBox(height: _expanded ? 8 : 6),
                          Wrap(
                            spacing: 6,
                            runSpacing: 6,
                            children: [
                              if (widget.card.distanceMeters case final meters?)
                                _DistanceTag(meters: meters),
                              ...store.tags.map((tag) => _TagChip(text: tag)),
                            ],
                          ),
                        ],
                        if (_expanded) ...[
                          const SizedBox(height: 14),
                          SizedBox(
                            height: 150,
                            child: _MenuList(
                              future: _menuFuture,
                              onPhotoTap: (menu) => showOriginalPhotoViewer(
                                context,
                                loadUrl: () =>
                                    _originalImages.menu(menu.id, menu.image!),
                              ),
                            ),
                          ),
                          const SizedBox(height: 14),
                          Row(
                            children: [
                              Expanded(
                                child: GestureDetector(
                                  onTap: _openDetail,
                                  child: Container(
                                    height: 46,
                                    decoration: BoxDecoration(
                                      color: Color(0xffF59E41),
                                      borderRadius: BorderRadius.circular(13),
                                    ),
                                    alignment: Alignment.center,
                                    child: Text(
                                      '상세보기',
                                      style: TextStyle(
                                        fontFamily: 'Paperlogy',
                                        fontWeight: FontWeight.w700,
                                        fontSize: 14,
                                        color: Color(0xff1F1F1F),
                                      ),
                                    ),
                                  ),
                                ),
                              ),
                              const SizedBox(width: 12),
                              Semantics(
                                button: true,
                                label: _bookmarked ? '찜 해제' : '찜하기',
                                child: IconButton(
                                  onPressed: _bookmarking ? null : _bookmark,
                                  style: IconButton.styleFrom(
                                    minimumSize: const Size(46, 46),
                                    backgroundColor: const Color(0xffF59E41)
                                        .withValues(alpha: 0.13),
                                    side: BorderSide(
                                      color: const Color(0xffF59E41)
                                          .withValues(alpha: 0.3),
                                    ),
                                    shape: RoundedRectangleBorder(
                                      borderRadius: BorderRadius.circular(13),
                                    ),
                                  ),
                                  icon: _bookmarking
                                      ? const SizedBox(
                                          width: 18,
                                          height: 18,
                                          child: CircularProgressIndicator(
                                            strokeWidth: 2,
                                          ),
                                        )
                                      : Icon(
                                          _bookmarked
                                              ? Icons.bookmark
                                              : Icons.bookmark_border,
                                          color: const Color(0xffF59E41),
                                        ),
                                ),
                              ),
                            ],
                          ),
                        ],
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _SwipeStamp extends StatelessWidget {
  final String text;
  final Color color;

  const _SwipeStamp({required this.text, required this.color});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      decoration: BoxDecoration(
        border: Border.all(color: color, width: 3),
        borderRadius: BorderRadius.circular(10),
        color: Colors.white.withValues(alpha: 0.92),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.15),
            blurRadius: 6,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: Text(
        text,
        style: TextStyle(
          fontFamily: 'Paperlogy',
          fontWeight: FontWeight.w800,
          fontSize: 26,
          letterSpacing: 2,
          color: color,
        ),
      ),
    );
  }
}

class _TagChip extends StatelessWidget {
  final String text;

  const _TagChip({required this.text});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: const Color(0xFFFDF3E8),
        border: Border.all(color: const Color(0xFFFBDDBE)),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        text,
        style: const TextStyle(
          fontFamily: 'Paperlogy',
          fontWeight: FontWeight.w600,
          fontSize: 11,
          color: Color(0xffB4650F),
        ),
      ),
    );
  }
}

class _DistanceTag extends StatelessWidget {
  final int meters;

  const _DistanceTag({required this.meters});

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
    decoration: BoxDecoration(
      color: const Color(0xFFF59E41),
      borderRadius: BorderRadius.circular(999),
    ),
    child: Text(
      meters <= 500
          ? '바로 근처'
          : meters <= 1000
          ? '근처'
          : meters <= 2000
          ? '조금 떨어진 곳'
          : '조금 먼 곳',
      style: const TextStyle(
        fontFamily: 'Paperlogy',
        fontWeight: FontWeight.w700,
        fontSize: 11,
        color: Colors.white,
      ),
    ),
  );
}

class _MenuList extends StatelessWidget {
  final Future<List<MenuItem>?>? future;
  final void Function(MenuItem) onPhotoTap;

  const _MenuList({required this.future, required this.onPhotoTap});

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<List<MenuItem>?>(
      future: future,
      builder: (context, snapshot) {
        if (snapshot.connectionState != ConnectionState.done) {
          return const Center(
            child: SizedBox(
              width: 22,
              height: 22,
              child: CircularProgressIndicator(
                strokeWidth: 2,
                color: Colors.black45,
              ),
            ),
          );
        }

        if (snapshot.hasError) {
          return const Text(
            '메뉴를 불러오지 못했습니다.',
            style: TextStyle(color: Colors.black54),
          );
        }

        final menus = snapshot.data;
        if (menus == null || menus.isEmpty) {
          return const Text(
            '등록된 메뉴가 없습니다.',
            style: TextStyle(color: Colors.black54),
          );
        }

        return ListView.separated(
          scrollDirection: Axis.horizontal,
          itemCount: menus.length,
          separatorBuilder: (_, _) => const SizedBox(width: 12),
          itemBuilder: (context, index) => MenuTile(
            menu: menus[index],
            onPhotoTap: () => onPhotoTap(menus[index]),
          ),
        );
      },
    );
  }
}
