import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:matchuri/models/restaurant_card.dart';
import 'package:matchuri/models/review.dart';
import 'package:matchuri/services/bookmark_api.dart';
import 'package:matchuri/services/restaurant_detail_api.dart';
import 'package:matchuri/services/original_card_images.dart';
import 'package:matchuri/widgets/directions_sheet.dart';
import 'package:matchuri/widgets/menu_tile.dart';
import 'package:matchuri/widgets/original_photo_viewer.dart';

class RestaurantDetailScreen extends StatefulWidget {
  final RestaurantCard card;
  final RestaurantDetailApi api;
  final BookmarkApi bookmarkApi;
  final bool initialBookmarked;
  final ValueChanged<bool>? onBookmarkChanged;

  const RestaurantDetailScreen({
    super.key,
    required this.card,
    required this.api,
    required this.bookmarkApi,
    this.initialBookmarked = false,
    this.onBookmarkChanged,
  });

  @override
  State<RestaurantDetailScreen> createState() => _RestaurantDetailScreenState();
}

class _RestaurantDetailScreenState extends State<RestaurantDetailScreen> {
  late Future<RestaurantCard> _detailFuture;
  late OriginalCardImages _originalImages;
  late RestaurantCard _bookmarkCard;
  int _cardVersion = 0;
  List<Review>? _allReviews;
  bool _loadingAllReviews = false;
  bool _reviewLoadFailed = false;
  bool get _bookmarked => widget.bookmarkApi.isBookmarked(
    _bookmarkCard,
    fallback: widget.initialBookmarked,
  );
  bool _bookmarkReady = false;
  bool _bookmarking = false;

  @override
  void initState() {
    super.initState();
    widget.bookmarkApi.addListener(_refreshBookmark);
    _resetCard();
  }

  void _refreshBookmark() {
    if (mounted) setState(() {});
  }

  void _resetCard() {
    final version = ++_cardVersion;
    _bookmarkCard = widget.card;
    _bookmarkReady = false;
    _bookmarking = false;
    _allReviews = null;
    _loadingAllReviews = false;
    _reviewLoadFailed = false;
    _originalImages = OriginalCardImages(
      api: widget.api,
      cardId: widget.card.cardId,
    );
    _detailFuture = widget.card.menus != null
        ? Future.value(widget.card)
        : widget.api.fetchCardDetail(widget.card.cardId);
    _detailFuture.then<void>(
      (detail) {
        if (!mounted || version != _cardVersion) return;
        setState(() {
          _bookmarkCard = detail;
          _bookmarkReady = true;
        });
      },
      onError: (Object error, StackTrace stack) {
        if (mounted && version == _cardVersion) {
          setState(() => _bookmarkReady = true);
        }
      },
    );
  }

  @override
  void didUpdateWidget(covariant RestaurantDetailScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.bookmarkApi != widget.bookmarkApi) {
      oldWidget.bookmarkApi.removeListener(_refreshBookmark);
      widget.bookmarkApi.addListener(_refreshBookmark);
    }
    if (oldWidget.card != widget.card ||
        oldWidget.api != widget.api ||
        oldWidget.bookmarkApi != widget.bookmarkApi) {
      _resetCard();
    }
  }

  @override
  void dispose() {
    widget.bookmarkApi.removeListener(_refreshBookmark);
    super.dispose();
  }

  Future<void> _toggleBookmark() async {
    if (!_bookmarkReady || _bookmarking) return;
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
      if (mounted) onChanged?.call(nextBookmarked);
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

  Future<void> _loadAllReviews() async {
    if (_loadingAllReviews) return;
    final version = _cardVersion;
    setState(() {
      _loadingAllReviews = true;
      _reviewLoadFailed = false;
    });
    try {
      final reviews = await widget.api.fetchReviews(widget.card.cardId);
      if (mounted && version == _cardVersion) {
        setState(() => _allReviews = reviews);
      }
    } catch (_) {
      if (mounted && version == _cardVersion) {
        setState(() => _reviewLoadFailed = true);
      }
    } finally {
      if (mounted && version == _cardVersion) {
        setState(() => _loadingAllReviews = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(
        backgroundColor: Colors.white,
        elevation: 0,
        foregroundColor: Colors.black,
        actions: [
          Semantics(
            button: true,
            label: _bookmarked ? '북마크 제거' : '북마크 추가',
            child: IconButton(
              key: const ValueKey('detail-bookmark-button'),
              onPressed: _bookmarkReady && !_bookmarking
                  ? _toggleBookmark
                  : null,
              tooltip: _bookmarked ? '북마크 제거' : '북마크 추가',
              icon: _bookmarking
                  ? const SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : SvgPicture.asset(
                      _bookmarked
                          ? 'assets/icons/bookmark_color.svg'
                          : 'assets/icons/bookmark.svg',
                      key: ValueKey(
                        _bookmarked
                            ? 'bookmark-icon-selected'
                            : 'bookmark-icon-unselected',
                      ),
                      width: 24,
                      height: 24,
                    ),
            ),
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: SafeArea(
        child: FutureBuilder<RestaurantCard>(
          future: _detailFuture,
          builder: (context, snapshot) {
            if (snapshot.connectionState != ConnectionState.done) {
              return const Center(child: CircularProgressIndicator());
            }

            if (snapshot.hasError) {
              return const Center(child: Text('상세 정보를 불러오지 못했습니다.'));
            }

            final detail = snapshot.data!;
            final store = detail.store;
            final menus = detail.menus;
            final initialReviews = store.reviews.isNotEmpty
                ? store.reviews
                : widget.card.store.reviews;
            final reviews = _allReviews ?? initialReviews.take(5).toList();
            final canLoadMore = _allReviews == null && store.reviewCount >= 6;
            final photos = detail.images.isNotEmpty
                ? detail.images
                : widget.card.images;

            return ListView(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 24),
              children: [
                if (detail.image != null)
                  Semantics(
                    button: true,
                    label: '대표 사진 크게 보기',
                    child: GestureDetector(
                      onTap: () => showOriginalPhotoViewer(
                        context,
                        loadUrl: () => _originalImages.hero(detail.image!),
                      ),
                      child: ClipRRect(
                        borderRadius: BorderRadius.circular(16),
                        child: Stack(
                          children: [
                            CachedNetworkImage(
                              imageUrl: detail.image!,
                              height: 220,
                              width: double.infinity,
                              fit: BoxFit.cover,
                              memCacheWidth:
                                  (MediaQuery.sizeOf(context).width *
                                          MediaQuery.devicePixelRatioOf(
                                            context,
                                          ))
                                      .round(),
                              fadeInDuration: const Duration(milliseconds: 150),
                              placeholder: (context, url) => Container(
                                height: 220,
                                color: Colors.grey.shade200,
                              ),
                              errorWidget: (context, url, error) => Container(
                                height: 220,
                                color: Colors.grey.shade300,
                              ),
                            ),
                            const Positioned(
                              right: 12,
                              bottom: 12,
                              child: DecoratedBox(
                                decoration: BoxDecoration(
                                  color: Colors.black54,
                                  shape: BoxShape.circle,
                                ),
                                child: Padding(
                                  padding: EdgeInsets.all(8),
                                  child: Icon(
                                    Icons.zoom_in,
                                    color: Colors.white,
                                    size: 22,
                                  ),
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                const SizedBox(height: 16),
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        store.name,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontFamily: 'Paperlogy',
                          fontWeight: FontWeight.w800,
                          fontSize: 24,
                        ),
                      ),
                    ),
                    SizedBox(width: 10),
                    if (store.location.isNotEmpty) ...[
                      const SizedBox(height: 12),
                      DirectionsButton(
                        userId: widget.api.userId,
                        cardId: detail.cardId,
                        storeName: store.name,
                        storeAddress: store.location,
                        latitude: detail.latitude,
                        longitude: detail.longitude,
                      ),
                    ],
                  ],
                ),
                const SizedBox(height: 4),
                Text(
                  store.category,
                  style: TextStyle(
                    fontFamily: 'Paperlogy',
                    fontWeight: FontWeight.w500,
                    fontSize: 14,
                    color: Colors.grey.shade600,
                  ),
                ),
                if (store.score != null) ...[
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      const Icon(Icons.star, size: 18, color: Colors.amber),
                      const SizedBox(width: 4),
                      Text(
                        store.score!.toStringAsFixed(1),
                        style: TextStyle(fontSize: 14),
                      ),
                    ],
                  ),
                ],
                const SizedBox(height: 8),
                Text(
                  store.location,
                  style: TextStyle(
                    fontFamily: 'Paperlogy',
                    fontWeight: FontWeight.w500,
                    color: Colors.grey.shade600,
                    fontSize: 14,
                  ),
                ),
                const SizedBox(height: 24),
                const Divider(height: 1, color: Color(0xffD1D5DB)),
                const SizedBox(height: 24),
                const Text(
                  '메뉴',
                  style: TextStyle(
                    fontFamily: 'Paperlogy',
                    fontWeight: FontWeight.w700,
                    fontSize: 18,
                  ),
                ),
                const SizedBox(height: 8),
                if (menus == null || menus.isEmpty)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 12),
                    child: Text(
                      '등록된 메뉴가 없습니다.',
                      style: TextStyle(color: Colors.grey.shade600),
                    ),
                  )
                else
                  SizedBox(
                    height: 150,
                    child: ListView.separated(
                      scrollDirection: Axis.horizontal,
                      itemCount: menus.length,
                      separatorBuilder: (_, _) => const SizedBox(width: 12),
                      itemBuilder: (context, index) => MenuTile(
                        menu: menus[index],
                        onPhotoTap: () => showOriginalPhotoViewer(
                          context,
                          loadUrl: () => _originalImages.menu(
                            menus[index].id,
                            menus[index].image!,
                          ),
                        ),
                      ),
                    ),
                  ),
                const SizedBox(height: 24),
                const Divider(height: 1, color: Color(0xffD1D5DB)),
                const SizedBox(height: 24),
                Text(
                  '리뷰 ${store.reviewCount}',
                  style: const TextStyle(
                    fontFamily: 'Paperlogy',
                    fontWeight: FontWeight.w700,
                    fontSize: 18,
                  ),
                ),
                const SizedBox(height: 8),
                if (photos.isNotEmpty) ...[
                  SizedBox(
                    height: 120,
                    child: ListView.separated(
                      scrollDirection: Axis.horizontal,
                      itemCount: photos.length,
                      separatorBuilder: (_, _) => const SizedBox(width: 8),
                      itemBuilder: (context, index) => _ReviewPhoto(
                        url: photos[index],
                        size: 120,
                        onTap: () => showOriginalPhotoViewer(
                          context,
                          loadUrl: () =>
                              _originalImages.gallery(index, photos[index]),
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                ],
                if (reviews.isEmpty)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 12),
                    child: Text(
                      '등록된 리뷰가 없습니다.',
                      style: TextStyle(color: Colors.grey.shade600),
                    ),
                  )
                else
                  ...reviews.map(
                    (review) => _ReviewCard(
                      review: review,
                      onPhotoTap: (index) => showOriginalPhotoViewer(
                        context,
                        loadUrl: () => _originalImages.review(
                          review.id,
                          index,
                          review.images[index],
                        ),
                      ),
                    ),
                  ),
                if (canLoadMore)
                  Center(
                    child: TextButton(
                      onPressed: _loadingAllReviews ? null : _loadAllReviews,
                      child: _loadingAllReviews
                          ? const SizedBox(
                              width: 18,
                              height: 18,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : Text(
                              _reviewLoadFailed ? '다시 시도' : '리뷰 더보기',
                              style: const TextStyle(
                                color: Colors.black,
                                fontFamily: 'Paperlogy',
                                fontWeight: FontWeight.w400,
                              ),
                            ),
                    ),
                  ),
              ],
            );
          },
        ),
      ),
    );
  }
}

class _ReviewCard extends StatelessWidget {
  final Review review;
  final void Function(int) onPhotoTap;

  const _ReviewCard({required this.review, required this.onPhotoTap});

  String _formatDate(DateTime date) {
    final y = date.year.toString().padLeft(4, '0');
    final m = date.month.toString().padLeft(2, '0');
    final d = date.day.toString().padLeft(2, '0');
    return '$y.$m.$d';
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: 16),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xffF9FAFB),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              if (review.score != null) ...[
                const Icon(Icons.star, size: 16, color: Colors.amber),
                const SizedBox(width: 4),
                Text(
                  review.score!.toStringAsFixed(1),
                  style: const TextStyle(
                    fontFamily: 'Paperlogy',
                    fontWeight: FontWeight.w600,
                    fontSize: 13,
                  ),
                ),
                const SizedBox(width: 8),
              ],
              if (review.date != null)
                Text(
                  _formatDate(review.date!),
                  style: TextStyle(
                    fontFamily: 'Paperlogy',
                    fontWeight: FontWeight.w500,
                    fontSize: 12,
                    color: Colors.grey.shade500,
                  ),
                ),
            ],
          ),
          if (review.content.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(
              review.content,
              style: const TextStyle(
                fontFamily: 'Paperlogy',
                fontWeight: FontWeight.w400,
                fontSize: 14,
                height: 1.5,
              ),
            ),
          ],
          if (review.images.isNotEmpty) ...[
            const SizedBox(height: 12),
            SizedBox(
              height: 120,
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                itemCount: review.images.length,
                separatorBuilder: (_, _) => const SizedBox(width: 8),
                itemBuilder: (context, index) => _ReviewPhoto(
                  url: review.images[index],
                  size: 120,
                  onTap: () => onPhotoTap(index),
                ),
              ),
            ),
          ],
          if (review.tags.isNotEmpty) ...[
            const SizedBox(height: 12),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: review.tags
                  .map(
                    (tag) => Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 10,
                        vertical: 4,
                      ),
                      decoration: BoxDecoration(
                        color: const Color(0xffF59E41).withValues(alpha: 0.12),
                        borderRadius: BorderRadius.circular(999),
                      ),
                      child: Text(
                        tag,
                        style: const TextStyle(
                          fontFamily: 'Paperlogy',
                          fontWeight: FontWeight.w600,
                          fontSize: 11,
                          color: Color(0xffB4650F),
                        ),
                      ),
                    ),
                  )
                  .toList(),
            ),
          ],
        ],
      ),
    );
  }
}

class _ReviewPhoto extends StatelessWidget {
  final String url;
  final double size;
  final VoidCallback onTap;

  const _ReviewPhoto({required this.url, this.size = 120, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final decodeSize = (size * MediaQuery.devicePixelRatioOf(context)).round();

    return Semantics(
      button: true,
      label: '사진 크게 보기',
      child: GestureDetector(
        onTap: onTap,
        child: ClipRRect(
          borderRadius: BorderRadius.circular(10),
          child: CachedNetworkImage(
            imageUrl: url,
            width: size,
            height: size,
            fit: BoxFit.cover,
            memCacheWidth: decodeSize,
            memCacheHeight: decodeSize,
            fadeInDuration: const Duration(milliseconds: 150),
            placeholder: (context, url) =>
                Container(color: Colors.grey.shade200),
            errorWidget: (context, url, error) =>
                Container(color: Colors.grey.shade300),
          ),
        ),
      ),
    );
  }
}
