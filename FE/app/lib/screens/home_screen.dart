import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_card_swiper/flutter_card_swiper.dart';
import 'package:matchuri/models/app_user.dart';
import 'package:matchuri/models/restaurant_card.dart';
import 'package:matchuri/screens/decision_proposal_dialog.dart';
import 'package:matchuri/screens/filter_screen.dart';
import 'package:matchuri/screens/restaurant_detail_screen.dart';
import 'package:matchuri/services/bookmark_api.dart';
import 'package:matchuri/services/location_service.dart';
import 'package:matchuri/services/preferences_api.dart';
import 'package:matchuri/services/restaurant_api.dart';
import 'package:matchuri/services/restaurant_detail_api.dart';
import 'package:matchuri/widgets/card_widget.dart';

class HomeScreen extends StatefulWidget {
  final RestaurantApi api;
  final BookmarkApi bookmarkApi;
  final RestaurantDetailApi detailApi;
  final String userId;
  final String userName;
  final PreferencesApi preferencesApi;
  final UserFilter? initialFilter;
  final VoidCallback? onBookmarkChanged;
  final VoidCallback? onTasteChanged;
  final Future<void> Function() onStartNewSession;

  const HomeScreen({
    super.key,
    required this.api,
    required this.bookmarkApi,
    required this.detailApi,
    required this.userId,
    this.userName = '',
    required this.preferencesApi,
    this.initialFilter,
    this.onBookmarkChanged,
    this.onTasteChanged,
    required this.onStartNewSession,
  });

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  static const _initialBatchSize = 3;
  static const _loadMoreBatchSize = 3;
  static const _loadMoreThreshold = 1;
  static const _precacheAhead = 2;

  RestaurantApi get _restaurantApi => widget.api;
  final CardSwiperController _swiperController = CardSwiperController();
  final LocationService _locationService = LocationService();

  List<RestaurantCard>? _cards;
  UserFilter? _filter;
  double? _decisionReadinessScore;
  bool _showRecommendationPrompt = false;
  bool _recommendationPromptDismissed = false;
  bool _wasDecisionReady = false;
  bool _hasError = false;
  bool _loadingMore = false;
  bool _requestingProposal = false;
  int? _currentCardIndex;
  Set<int> _bookmarkedCardIds = {};
  Future<void> _swipeQueue = Future.value();
  Future<void> _locationQueue = Future.value();

  final List<RestaurantCard> likedCards = [];
  final List<RestaurantCard> dislikedCards = [];

  @override
  void initState() {
    super.initState();
    _filter = widget.initialFilter;
    _loadCards();
    _loadDecisionReadiness();
  }

  Future<void> _loadDecisionReadiness() async {
    try {
      final score = await _restaurantApi.fetchDecisionReadinessScore();
      debugPrint('decision readiness: $score');
      if (mounted) {
        final isDecisionReady = score >= 80.0;
        setState(() {
          _decisionReadinessScore = score;
          if (!isDecisionReady) _recommendationPromptDismissed = false;
          _showRecommendationPrompt =
              isDecisionReady &&
              !_wasDecisionReady &&
              !_recommendationPromptDismissed;
          _wasDecisionReady = isDecisionReady;
        });
      }
    } catch (error) {
      debugPrint('Failed to load decision readiness: $error');
    }
  }

  Future<void> _editFilters() async {
    final filter = await Navigator.of(context).push<UserFilter>(
      MaterialPageRoute(
        builder: (_) => FilterScreen(
          userId: widget.userId,
          sessionId: widget.api.sessionId,
          userService: widget.preferencesApi,
          initialFilter: _filter,
          onCompleted: (filter) async => Navigator.of(context).pop(filter),
        ),
      ),
    );
    if (filter == null || !mounted) return;
    setState(() {
      _filter = filter;
      _cards = null;
      _currentCardIndex = null;
      _hasError = false;
    });
    await _loadCards();
  }

  String _selectionLabel(List<String> values, String fallback) {
    if (values.isEmpty) return '# $fallback';
    final remaining = values.length - 1;
    return '# ${values.first}${remaining > 0 ? ' 외 $remaining개' : ''}';
  }

  Widget _buildFilterSummary() {
    final filter = _filter;
    if (filter == null) return const SizedBox.shrink();
    final labels = [
      _selectionLabel(filter.categories, '전체'),
      filter.withinMeters == null
          ? '# 거리 무관'
          : '# ${_distanceLabel(filter.withinMeters!)} 이내',
      _selectionLabel(filter.tags, '전체'),
    ];
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 16),
      child: SizedBox(
        height: 42,
        child: Stack(
          children: [
            Center(
              child: Padding(
                padding: const EdgeInsets.only(right: 44),
                child: FittedBox(
                  fit: BoxFit.scaleDown,
                  child: Row(
                    children: labels
                        .map(
                          (label) => Container(
                            margin: const EdgeInsets.only(right: 8),
                            padding: const EdgeInsets.symmetric(
                              horizontal: 16,
                              vertical: 9,
                            ),
                            decoration: BoxDecoration(
                              color: const Color(0xFFFDF3E8),
                              border: Border.all(
                                color: const Color(0xFFFBDDBE),
                              ),
                              borderRadius: BorderRadius.circular(20),
                            ),
                            child: Text(
                              label,
                              style: const TextStyle(
                                color: Color(0xFFF59E41),
                                fontFamily: 'Paperlogy',
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                          ),
                        )
                        .toList(),
                  ),
                ),
              ),
            ),
            Positioned(
              right: 0,
              top: -5,
              child: Semantics(
                button: true,
                label: '필터 다시 선택하기',
                child: IconButton(
                  onPressed: _editFilters,
                  icon: const Icon(Icons.tune, color: Color(0xFFF59E41)),
                  constraints: const BoxConstraints.tightFor(
                    width: 36,
                    height: 36,
                  ),
                  padding: EdgeInsets.zero,
                  tooltip: '필터 다시 선택하기',
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  String _distanceLabel(int meters) =>
      meters < 1000 ? '${meters}m' : '${meters / 1000}km';

  Future<void> _loadCards() async {
    try {
      final cardsFuture = _restaurantApi.fetchCards(limit: _initialBatchSize);
      final bookmarkIdsFuture = _fetchBookmarkIds();
      final firstBatch = await cardsFuture;
      final bookmarkIds = await bookmarkIdsFuture;
      if (!mounted) return;
      setState(() {
        _cards = firstBatch;
        _bookmarkedCardIds = bookmarkIds;
        _currentCardIndex = 0;
      });
      _precacheFrom(firstBatch, 0);
    } catch (_) {
      if (!mounted) return;
      setState(() => _hasError = true);
      return;
    }
  }

  Future<Set<int>> _fetchBookmarkIds() async {
    try {
      return (await widget.bookmarkApi.fetchBookmarks())
          .map((card) => card.cardId)
          .toSet();
    } catch (error) {
      debugPrint('Failed to load bookmarks: $error');
      return {};
    }
  }

  Future<void> _requestRecommendations() async {
    if (_requestingProposal) return;
    setState(() {
      _requestingProposal = true;
      _showRecommendationPrompt = false;
    });
    try {
      final proposal = await _restaurantApi.fetchDecisionProposal();
      if (!mounted) return;
      if (!proposal.ready || proposal.cards.isEmpty) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              proposal.message.isEmpty ? '아직 추천을 준비 중이에요.' : proposal.message,
            ),
          ),
        );
        return;
      }
      final result = await showDecisionProposalDialog(context, proposal);
      if (!mounted || result == null) return;
      if (result.resetSession) {
        await widget.onStartNewSession();
      } else if (result.selectedCard != null) {
        Navigator.of(context).push(
          MaterialPageRoute(
            builder: (_) => RestaurantDetailScreen(
              card: result.selectedCard!,
              api: widget.detailApi,
              bookmarkApi: widget.bookmarkApi,
              onBookmarkChanged: (_) => widget.onBookmarkChanged?.call(),
            ),
          ),
        );
      }
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('추천 목록을 불러오지 못했어요.')));
      }
      debugPrint('Failed to load decision proposal: $error');
    } finally {
      if (mounted) setState(() => _requestingProposal = false);
    }
  }

  Future<void> _loadMoreCards(
    List<RestaurantCard> cards,
    int currentIndex,
  ) async {
    if (_loadingMore || cards.length - currentIndex > _loadMoreThreshold) {
      return;
    }

    _loadingMore = true;
    try {
      final nextBatch = await _restaurantApi.fetchCards(
        limit: _loadMoreBatchSize,
      );
      if (!mounted || !identical(_cards, cards)) return;
      final knownIds = cards.map((card) => card.cardId).toSet();
      final extra = nextBatch.where((card) => !knownIds.contains(card.cardId));
      if (extra.isNotEmpty) {
        final updatedCards = [...cards, ...extra];
        final resumeIndex = _currentCardIndex == null ? cards.length : null;
        setState(() {
          _cards = updatedCards;
        });
        _precacheFrom(updatedCards, currentIndex);
        if (resumeIndex != null) {
          WidgetsBinding.instance.addPostFrameCallback((_) {
            if (!mounted ||
                !identical(_cards, updatedCards) ||
                _currentCardIndex != null) {
              return;
            }
            _currentCardIndex = resumeIndex;
            _swiperController.moveTo(resumeIndex);
          });
        }
      }
    } catch (error) {
      debugPrint('Failed to load more recommendations: $error');
    } finally {
      _loadingMore = false;
    }
  }

  void _precacheFrom(List<RestaurantCard> cards, int fromIndex) {
    final lastIndex = (fromIndex + _precacheAhead).clamp(0, cards.length);
    final cacheWidth =
        (MediaQuery.sizeOf(context).width *
                MediaQuery.devicePixelRatioOf(context))
            .round();
    for (final card in cards.sublist(fromIndex, lastIndex)) {
      final image = card.image;
      if (image != null) {
        precacheImage(
          ResizeImage(CachedNetworkImageProvider(image), width: cacheWidth),
          context,
        );
      }
    }
  }

  void _recordSwipe(
    List<RestaurantCard> cards,
    int cardId,
    String prefer,
    int? currentIndex,
  ) {
    _updateLocation();
    _swipeQueue = _swipeQueue.then((_) async {
      try {
        final result = await _restaurantApi.postSwipeLog(
          cardId: cardId,
          prefer: prefer,
        );
        // The card animation completes before this request. Notify MyScreen
        // only after the server has accepted the swipe, avoiding a stale
        // fetch when the user switches tabs immediately.
        if (mounted) widget.onTasteChanged?.call();
        await _loadDecisionReadiness();
        debugPrint('learned: ${result['learned']}');
        if (currentIndex != null) _loadMoreCards(cards, currentIndex);
      } catch (error) {
        debugPrint('스와이프 로그 전송 실패: $error');
      }
    });
  }

  void _updateLocation() {
    _locationQueue = _locationQueue.then((_) async {
      try {
        final location = await _locationService.getCurrentLocation();
        if (location == null) return;
        await widget.preferencesApi.saveLocation(
          userId: widget.userId,
          sessionId: widget.api.sessionId,
          latitude: location.lat,
          longitude: location.lng,
        );
      } catch (error) {
        debugPrint('Failed to update location after swipe: $error');
      }
    });
  }

  Future<bool> _onSwipe(
    List<RestaurantCard> cards,
    int previousIndex,
    int? currentIndex,
    CardSwiperDirection direction,
  ) {
    final card = cards[previousIndex];
    _currentCardIndex = currentIndex;

    String? prefer;
    if (direction == CardSwiperDirection.right) {
      likedCards.add(card);
      prefer = 'like';
      debugPrint('선호: ${card.store.name} (총 ${likedCards.length}개)');
    } else if (direction == CardSwiperDirection.left) {
      dislikedCards.add(card);
      prefer = 'dislike';
      debugPrint('비선호: ${card.store.name} (총 ${dislikedCards.length}개)');
    }

    if (prefer != null) _recordSwipe(cards, card.cardId, prefer, currentIndex);

    if (currentIndex != null) {
      _precacheFrom(cards, currentIndex);
    }

    return Future.value(true);
  }

  @override
  void dispose() {
    _swiperController.dispose();
    super.dispose();
  }

  Widget _buildBody() {
    if (_hasError) {
      return Center(child: Text('식당 정보를 불러오지 못했습니다.'));
    }

    final cards = _cards;
    if (cards == null) {
      return Center(child: CircularProgressIndicator());
    }

    if (cards.isEmpty) {
      return Center(child: Text('표시할 식당이 없습니다.'));
    }

    return CardSwiper(
      key: ValueKey(_filter),
      controller: _swiperController,
      allowedSwipeDirection: AllowedSwipeDirection.only(
        left: true,
        right: true,
      ),
      maxAngle: 30,
      cardsCount: cards.length,
      isLoop: false,
      numberOfCardsDisplayed: cards.length >= 2 ? 2 : 1,
      onSwipe: (previousIndex, currentIndex, direction) =>
          _onSwipe(cards, previousIndex, currentIndex, direction),
      cardBuilder: (context, index, percentThresholdX, percentThresholdY) =>
          CardWidget(
            key: ValueKey(cards[index].cardId),
            card: cards[index],
            bookmarkApi: widget.bookmarkApi,
            detailApi: widget.detailApi,
            initialBookmarked: _bookmarkedCardIds.contains(cards[index].cardId),
            onBookmarkChanged: widget.onBookmarkChanged,
            swipePercent: percentThresholdX,
          ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      body: Stack(
        children: [
          SafeArea(
            child: Column(
              children: [
                _buildFilterSummary(),
                _DecisionReadiness(
                  score: _decisionReadinessScore,
                  onRequestRecommendations: _requestRecommendations,
                  isRequestingProposal: _requestingProposal,
                ),
                Expanded(child: _buildBody()),
              ],
            ),
          ),
          if (_showRecommendationPrompt)
            _RecommendationReadyOverlay(
              userName: widget.userName,
              onRecommend: _requestRecommendations,
              onKeepSwiping: () => setState(() {
                _recommendationPromptDismissed = true;
                _showRecommendationPrompt = false;
              }),
            ),
        ],
      ),
    );
  }
}

class _DecisionReadiness extends StatelessWidget {
  final double? score;
  final Future<void> Function() onRequestRecommendations;
  final bool isRequestingProposal;

  const _DecisionReadiness({
    required this.score,
    required this.onRequestRecommendations,
    required this.isRequestingProposal,
  });

  @override
  Widget build(BuildContext context) {
    if (score == null) return const SizedBox.shrink();
    final percentage = (score!.clamp(0.0, 80.0) / 80.0 * 100.0).clamp(
      0.0,
      100.0,
    );
    final progress = percentage / 100;
    final percentageLabel = percentage % 1 == 0
        ? percentage.toStringAsFixed(0)
        : percentage.toStringAsFixed(1);
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (percentage >= 100 && isRequestingProposal) ...[
            Container(
              width: double.infinity,
              margin: const EdgeInsets.only(bottom: 12),
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                color: const Color(0xFFFFF7ED),
                border: Border.all(color: const Color(0xFFF9A13A), width: 2),
                borderRadius: BorderRadius.circular(18),
              ),
              child: Row(
                children: [
                  const Icon(
                    Icons.auto_awesome_rounded,
                    color: Color(0xFFF59E41),
                    size: 28,
                  ),
                  const SizedBox(width: 10),
                  const Expanded(
                    child: Text(
                      '취향 분석이 완료됐어요!\n맞춤 추천을 받아보세요.',
                      style: TextStyle(
                        color: Color(0xFFB56B22),
                        fontFamily: 'Paperlogy',
                        fontWeight: FontWeight.w700,
                        fontSize: 13,
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  FilledButton(
                    onPressed: isRequestingProposal
                        ? null
                        : onRequestRecommendations,
                    style: FilledButton.styleFrom(
                      backgroundColor: const Color(0xFFF9A13A),
                      foregroundColor: Colors.white,
                      minimumSize: const Size(80, 42),
                      padding: const EdgeInsets.symmetric(horizontal: 12),
                    ),
                    child: isRequestingProposal
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: Colors.white,
                            ),
                          )
                        : const Text('추천받기'),
                  ),
                ],
              ),
            ),
          ],
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              const Text(
                '취향 수집도',
                style: TextStyle(
                  fontFamily: 'Paperlogy',
                  fontWeight: FontWeight.w700,
                  fontSize: 13,
                ),
              ),
              Text(
                '$percentageLabel%',
                style: const TextStyle(
                  color: Color(0xffF59E41),
                  fontFamily: 'Paperlogy',
                  fontWeight: FontWeight.w700,
                  fontSize: 13,
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          LinearProgressIndicator(
            value: progress,
            minHeight: 7,
            borderRadius: BorderRadius.circular(8),
            color: const Color(0xffF59E41),
            backgroundColor: const Color(0xffF2F2F2),
          ),
        ],
      ),
    );
  }
}

class _RecommendationReadyOverlay extends StatelessWidget {
  final String userName;
  final Future<void> Function() onRecommend;
  final VoidCallback onKeepSwiping;

  const _RecommendationReadyOverlay({
    required this.userName,
    required this.onRecommend,
    required this.onKeepSwiping,
  });

  @override
  Widget build(BuildContext context) => Positioned.fill(
    child: ColoredBox(
      color: const Color(0xB3000000),
      child: Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(
                Icons.auto_awesome_rounded,
                color: Color(0xFFF9A13A),
                size: 42,
              ),
              const SizedBox(height: 20),
              Text(
                '${userName.isEmpty ? '회원' : userName}님의 취향을 기반으로\n식당 추천이 완료되었어요.',
                textAlign: TextAlign.center,
                style: const TextStyle(
                  color: Colors.white,
                  fontFamily: 'Paperlogy',
                  fontWeight: FontWeight.w800,
                  fontSize: 23,
                  height: 1.35,
                ),
              ),
              const SizedBox(height: 10),
              const Text(
                '지금 가장 잘 맞는 식당을 골라드릴게요.',
                textAlign: TextAlign.center,
                style: TextStyle(
                  color: Color(0xFFD1D5DB),
                  fontFamily: 'Paperlogy',
                  fontSize: 14,
                ),
              ),
              const SizedBox(height: 28),
              SizedBox(
                width: double.infinity,
                height: 52,
                child: FilledButton(
                  onPressed: onRecommend,
                  style: FilledButton.styleFrom(
                    backgroundColor: const Color(0xFFF59E41),
                    foregroundColor: Colors.white,
                  ),
                  child: const Text(
                    '추천 받기',
                    style: TextStyle(
                      fontFamily: 'Paperlogy',
                      fontWeight: FontWeight.w800,
                      fontSize: 16,
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 8),
              TextButton(
                onPressed: onKeepSwiping,
                child: const Text(
                  '좀 더 스와이프하기',
                  style: TextStyle(
                    color: Colors.white,
                    fontFamily: 'Paperlogy',
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    ),
  );
}
