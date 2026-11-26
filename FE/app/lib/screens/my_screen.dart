import 'dart:math' as math;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:matchuri/models/app_user.dart';
import 'package:matchuri/models/restaurant_card.dart';
import 'package:matchuri/models/store.dart';
import 'package:matchuri/models/taste_map.dart';
import 'package:matchuri/screens/restaurant_detail_screen.dart';
import 'package:matchuri/services/bookmark_api.dart';
import 'package:matchuri/services/restaurant_detail_api.dart';
import 'package:matchuri/services/taste_api.dart';

class MyScreen extends StatefulWidget {
  final AppUser user;
  final TasteApi tasteApi;
  final RestaurantDetailApi detailApi;
  final BookmarkApi bookmarkApi;
  final ValueListenable<int> refreshVersion;
  final VoidCallback? onBookmarkChanged;

  const MyScreen({
    super.key,
    required this.user,
    required this.tasteApi,
    required this.detailApi,
    required this.bookmarkApi,
    required this.refreshVersion,
    this.onBookmarkChanged,
  });

  @override
  State<MyScreen> createState() => _MyScreenState();
}

class _MyScreenState extends State<MyScreen> {
  late Future<TasteMap> _taste;

  @override
  void initState() {
    super.initState();
    _taste = widget.tasteApi.fetchTaste(
      widget.user.userId,
      widget.user.sessionId!,
    );
    widget.refreshVersion.addListener(_refresh);
  }

  void _refresh() {
    setState(() {
      _taste = widget.tasteApi.fetchTaste(
        widget.user.userId,
        widget.user.sessionId!,
      );
    });
  }

  @override
  void dispose() {
    widget.refreshVersion.removeListener(_refresh);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    backgroundColor: Colors.white,
    body: SafeArea(
      child: FutureBuilder<TasteMap>(
        future: _taste,
        builder: (context, snapshot) {
          if (snapshot.connectionState != ConnectionState.done) {
            return const Center(
              child: CircularProgressIndicator(color: Color(0xffF59E41)),
            );
          }
          if (!snapshot.hasData) {
            return const Center(child: Text('취향 정보를 불러오지 못했어요.'));
          }
          final taste = snapshot.data!;
          return ListView(
            padding: const EdgeInsets.fromLTRB(24, 22, 24, 28),
            children: [
              Text(
                '${widget.user.name}님,\n요즘은 어떤 게 끌리나요?',
                style: const TextStyle(
                  fontFamily: 'Paperlogy',
                  fontWeight: FontWeight.w800,
                  fontSize: 25,
                  height: 1.35,
                ),
              ),
              const SizedBox(height: 22),
              _TasteMapView(
                taste: taste,
                tasteApi: widget.tasteApi,
                detailApi: widget.detailApi,
                bookmarkApi: widget.bookmarkApi,
                onBookmarkChanged: widget.onBookmarkChanged,
              ),
              const SizedBox(height: 22),
              Row(
                children: [
                  _Stat(label: '전체 스와이프', value: '${taste.swipes}회'),
                  const SizedBox(width: 10),
                  _Stat(label: '땡겨요', value: '${taste.likes}회'),
                ],
              ),
              if (taste.drift != null) ...[
                const SizedBox(height: 14),
                _Trend(drift: taste.drift!),
              ],
            ],
          );
        },
      ),
    ),
  );
}

class _TasteMapView extends StatelessWidget {
  final TasteMap taste;
  final TasteApi tasteApi;
  final RestaurantDetailApi detailApi;
  final BookmarkApi bookmarkApi;
  final VoidCallback? onBookmarkChanged;

  const _TasteMapView({
    required this.taste,
    required this.tasteApi,
    required this.detailApi,
    required this.bookmarkApi,
    this.onBookmarkChanged,
  });

  void _openFullMap(BuildContext context) {
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => _DetailedTasteMapScreen(
          taste: taste,
          tasteApi: tasteApi,
          detailApi: detailApi,
          bookmarkApi: bookmarkApi,
          onBookmarkChanged: onBookmarkChanged,
        ),
      ),
    );
  }

  void _openDetail(BuildContext context, TasteNearbyRestaurant restaurant) {
    final cardId = restaurant.cardId;
    if (cardId == null) return;
    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => RestaurantDetailScreen(
          api: detailApi,
          bookmarkApi: bookmarkApi,
          onBookmarkChanged: (_) => onBookmarkChanged?.call(),
          card: RestaurantCard(
            cardId: cardId,
            image: restaurant.image,
            store: Store(
              id: restaurant.storeId,
              name: restaurant.name,
              location: restaurant.location,
              category: restaurant.category,
              score: null,
            ),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (taste.trail.isEmpty && taste.now == null) {
      return _TasteMessage(
        message: taste.swipes == 0
            ? '아직 취향 데이터가 없어요.\n식당을 평가하면 취향 지도가 만들어져요.'
            : '취향 지도를 만들기엔 데이터가 조금 부족해요.',
      );
    }
    final nearby = taste.now?.near.take(3).toList() ?? const [];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text(
          '나의 취향 지도',
          style: TextStyle(
            fontFamily: 'Paperlogy',
            fontWeight: FontWeight.w700,
            fontSize: 17,
          ),
        ),
        const SizedBox(height: 8),
        SizedBox(
          key: const ValueKey('taste-map-canvas'),
          height: 320,
          child: Stack(
            children: [
              Positioned.fill(
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(20),
                  child: ColoredBox(
                    color: const Color(0xffFFF9F3),
                    child: LayoutBuilder(
                      builder: (context, constraints) => InteractiveViewer(
                        minScale: 1,
                        maxScale: 3,
                        boundaryMargin: const EdgeInsets.all(48),
                        child: CustomPaint(
                          size: constraints.biggest,
                          painter: _TasteMapPainter(
                            regions: taste.regions,
                            trail: taste.trail,
                            now: taste.now,
                            affinities: _regionAffinities(taste),
                          ),
                        ),
                      ),
                    ),
                  ),
                ),
              ),
              Positioned(
                top: 10,
                right: 10,
                child: Material(
                  color: Colors.white.withValues(alpha: .92),
                  elevation: 1,
                  shape: const CircleBorder(),
                  child: IconButton(
                    tooltip: '취향 지도 크게 보기',
                    onPressed: () => _openFullMap(context),
                    icon: const Icon(Icons.open_in_full_rounded),
                    color: const Color(0xffB4650F),
                  ),
                ),
              ),
            ],
          ),
        ),
        if (taste.headline.isNotEmpty) ...[
          const SizedBox(height: 12),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
            decoration: BoxDecoration(
              color: const Color(0xffFDF3E8),
              borderRadius: BorderRadius.circular(14),
            ),
            child: Text(
              taste.headline,
              style: const TextStyle(
                fontFamily: 'Paperlogy',
                fontWeight: FontWeight.w700,
                fontSize: 14,
                height: 1.4,
              ),
            ),
          ),
        ],
        if (nearby.isNotEmpty) ...[
          const SizedBox(height: 14),
          const Text(
            '지금 취향과 가까운 식당',
            style: TextStyle(
              fontFamily: 'Paperlogy',
              fontWeight: FontWeight.w700,
              fontSize: 13,
            ),
          ),
          const SizedBox(height: 6),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final restaurant in nearby)
                ActionChip(
                  avatar: const Icon(
                    Icons.restaurant_rounded,
                    size: 16,
                    color: Color(0xffB4650F),
                  ),
                  label: Text(restaurant.name),
                  onPressed: restaurant.cardId == null
                      ? null
                      : () => _openDetail(context, restaurant),
                  backgroundColor: const Color(0xffFFF8EF),
                  side: const BorderSide(color: Color(0xffFBDDBE)),
                  labelStyle: const TextStyle(
                    fontFamily: 'Paperlogy',
                    fontSize: 12,
                  ),
                ),
            ],
          ),
        ],
      ],
    );
  }
}

class _DetailedTasteMapScreen extends StatefulWidget {
  final TasteMap taste;
  final TasteApi tasteApi;
  final RestaurantDetailApi detailApi;
  final BookmarkApi bookmarkApi;
  final VoidCallback? onBookmarkChanged;

  const _DetailedTasteMapScreen({
    required this.taste,
    required this.tasteApi,
    required this.detailApi,
    required this.bookmarkApi,
    this.onBookmarkChanged,
  });

  @override
  State<_DetailedTasteMapScreen> createState() =>
      _DetailedTasteMapScreenState();
}

class _DetailedTasteMapScreenState extends State<_DetailedTasteMapScreen> {
  final TransformationController _transform = TransformationController();
  final List<_TasteMapLayer> _layers = [];
  double _scale = 1;
  bool _loading = true;
  String? _error;
  String? _retryParent;
  TasteMapNode? _retryAnchor;
  String _retryTitle = '전체 취향';

  _TasteMapLayer? get _layer => _layers.isEmpty ? null : _layers.last;

  @override
  void initState() {
    super.initState();
    _transform.addListener(_updateScale);
    _loadLayer(parent: null, title: '전체 취향');
  }

  @override
  void dispose() {
    _transform.removeListener(_updateScale);
    _transform.dispose();
    super.dispose();
  }

  void _updateScale() {
    final scale = _transform.value.getMaxScaleOnAxis();
    if (mounted && (scale - _scale).abs() > .01) {
      setState(() => _scale = scale);
    }
  }

  Future<void> _loadLayer({
    required String? parent,
    required String title,
    TasteMapNode? anchor,
  }) async {
    setState(() {
      _loading = true;
      _error = null;
      _retryParent = parent;
      _retryAnchor = anchor;
      _retryTitle = title;
    });
    try {
      final data = await widget.tasteApi.fetchMapChildren(parent: parent);
      if (!mounted) return;
      setState(() {
        _layers.add(_TasteMapLayer(title: title, data: data, anchor: anchor));
        _loading = false;
      });
      _resetTransform();
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = '상세 취향 지도를 불러오지 못했어요.';
      });
      debugPrint('Failed to load detailed taste map: $error');
    }
  }

  void _resetTransform() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _transform.value = Matrix4.identity();
    });
  }

  void _goBack() {
    if (_layers.length <= 1) {
      Navigator.of(context).pop();
      return;
    }
    setState(() {
      _layers.removeLast();
      _error = null;
    });
    _resetTransform();
  }

  void _selectNode(TasteMapNode node) {
    if (_loading) return;
    if (node.cardId != null) {
      Navigator.of(context).push(
        MaterialPageRoute(
          builder: (_) => RestaurantDetailScreen(
            api: widget.detailApi,
            bookmarkApi: widget.bookmarkApi,
            onBookmarkChanged: (_) => widget.onBookmarkChanged?.call(),
            card: RestaurantCard(
              cardId: node.cardId!,
              image: null,
              store: Store(
                id: '',
                name: node.name,
                location: '',
                category: _layer?.title ?? '',
                score: null,
              ),
            ),
          ),
        ),
      );
      return;
    }
    _loadLayer(parent: node.key, title: node.name, anchor: node);
  }

  Map<String, double> _nodeAffinities(List<TasteMapNode> nodes) {
    if (nodes.isEmpty || widget.taste.trail.isEmpty) return const {};

    final regionCounts = <String, int>{};
    for (final point in widget.taste.trail) {
      if (point.region.isNotEmpty) {
        regionCounts.update(
          point.region,
          (value) => value + 1,
          ifAbsent: () => 1,
        );
      }
    }
    if (_layer?.data.level == 'cat2' && regionCounts.isNotEmpty) {
      final maximum = regionCounts.values.reduce(math.max);
      return {
        for (final node in nodes)
          node.key: (regionCounts[node.name] ?? 0) / maximum,
      };
    }

    return const {};
  }

  @override
  Widget build(BuildContext context) {
    final layer = _layer;
    return PopScope(
      canPop: _layers.length <= 1,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) _goBack();
      },
      child: Scaffold(
        backgroundColor: const Color(0xffFFF9F3),
        appBar: AppBar(
          centerTitle: false,
          titleSpacing: 0,
          leadingWidth: 48,
          toolbarHeight: 64,
          backgroundColor: Colors.white,
          surfaceTintColor: Colors.white,
          leading: IconButton(
            tooltip: _layers.length > 1 ? '이전 단계' : '닫기',
            onPressed: _goBack,
            icon: Icon(
              _layers.length > 1
                  ? Icons.arrow_back_rounded
                  : Icons.close_rounded,
            ),
          ),
          title: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                layer?.title ?? '상세 취향 지도',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  fontFamily: 'Paperlogy',
                  fontWeight: FontWeight.w800,
                  fontSize: 18,
                ),
              ),
              if (layer != null)
                Text(
                  switch (layer.data.level) {
                    'cat2' => '업종을 눌러 더 자세히 살펴보세요',
                    'cat3' => '세부 업종을 눌러 식당을 확인하세요',
                    _ => '식당을 누르면 상세 정보를 볼 수 있어요',
                  },
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: Color(0xff8A817A),
                    fontSize: 11,
                    fontWeight: FontWeight.w500,
                  ),
                ),
            ],
          ),
          actions: [
            TextButton.icon(
              onPressed: _resetTransform,
              icon: const Icon(Icons.center_focus_strong_rounded, size: 18),
              label: const Text('원위치'),
              style: TextButton.styleFrom(
                foregroundColor: const Color(0xff705B49),
              ),
            ),
          ],
        ),
        body: Column(
          children: [
            Expanded(
              child: Stack(
                children: [
                  if (layer != null)
                    Positioned.fill(
                      child: LayoutBuilder(
                        builder: (context, constraints) {
                          final mapSize = constraints.biggest;
                          return GestureDetector(
                            behavior: HitTestBehavior.opaque,
                            onTapUp: (details) {
                              final node = _nearestNode(
                                  _transform.toScene(details.localPosition),
                                  mapSize,
                                  layer.data.nodes,
                                  layer.data.level,
                                  _scale,
                                  layer.anchor,
                                );
                              if (node != null) _selectNode(node);
                            },
                            child: InteractiveViewer(
                              transformationController: _transform,
                              minScale: .85,
                              maxScale: 8,
                              boundaryMargin: EdgeInsets.all(
                                mapSize.shortestSide * 10,
                              ),
                              child: CustomPaint(
                                size: mapSize,
                                painter: _DetailedTasteMapPainter(
                                  nodes: layer.data.nodes,
                                  level: layer.data.level,
                                  category: _layers.length > 1
                                      ? _layers[1].title
                                      : null,
                                  trail: widget.taste.trail,
                                  now: widget.taste.now,
                                  affinities: _nodeAffinities(layer.data.nodes),
                                  scale: _scale,
                                  anchor: layer.anchor,
                                ),
                              ),
                            ),
                          );
                        },
                      ),
                    ),
                  if (_loading)
                    const Positioned.fill(
                      child: ColoredBox(
                        color: Color(0x55FFF9F3),
                        child: Center(
                          child: CircularProgressIndicator(
                            color: Color(0xffF59E41),
                          ),
                        ),
                      ),
                    ),
                  if (_error != null && !_loading)
                    Center(
                      child: FilledButton.icon(
                        onPressed: () => _loadLayer(
                          parent: _retryParent,
                          title: _retryTitle,
                          anchor: _retryAnchor,
                        ),
                        icon: const Icon(Icons.refresh_rounded),
                        label: Text(_error!),
                      ),
                    ),
                ],
              ),
            ),
            _TasteMapLegend(detailed: layer?.data.level != 'cat2'),
          ],
        ),
      ),
    );
  }
}

class _TasteMapLayer {
  final String title;
  final TasteMapChildren data;
  final TasteMapNode? anchor;

  const _TasteMapLayer({
    required this.title,
    required this.data,
    this.anchor,
  });
}

class _TasteMapLegend extends StatelessWidget {
  final bool detailed;

  const _TasteMapLegend({required this.detailed});

  @override
  Widget build(BuildContext context) => SafeArea(
    top: false,
    child: Container(
      width: double.infinity,
      margin: const EdgeInsets.fromLTRB(12, 0, 12, 12),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: const Color(0xffEAE5DF)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          const Wrap(
            spacing: 16,
            runSpacing: 8,
            children: [
              _LegendItem(
                icon: Icons.bubble_chart_rounded,
                color: Color(0xff168678),
                label: '색 원 · 음식 분류',
              ),
              _LegendItem(
                icon: Icons.trending_flat_rounded,
                color: Color(0xff344467),
                label: '점·화살표 · 취향 변화',
              ),
              _LegendItem(
                icon: Icons.my_location_rounded,
                color: Color(0xff344467),
                label: '지금 · 현재 취향',
              ),
            ],
          ),
          const SizedBox(height: 10),
          Text(
            detailed
                ? '같은 색은 같은 상위 분류예요. 식당 수는 좋아요 수가 아니에요.'
                : '진하고 굵은 원일수록 최근 궤적에 자주 나타난 분류예요. 원의 크기는 선호도가 아니에요.',
            style: const TextStyle(
              fontSize: 12,
              height: 1.5,
              color: Color(0xff595F6B),
            ),
          ),
          const SizedBox(height: 4),
          const Text(
            '화살표는 시간 순서예요 · 두 손가락으로 확대, 끌어서 이동',
            style: TextStyle(
              fontSize: 11,
              height: 1.5,
              color: Color(0xff757B86),
            ),
          ),
        ],
      ),
    ),
  );
}

class _LegendItem extends StatelessWidget {
  final IconData icon;
  final Color color;
  final String label;

  const _LegendItem({
    required this.icon,
    required this.color,
    required this.label,
  });

  @override
  Widget build(BuildContext context) => Row(
    mainAxisSize: MainAxisSize.min,
    children: [
      Icon(icon, color: color, size: 18),
      const SizedBox(width: 5),
      Text(
        label,
        style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w600),
      ),
    ],
  );
}

const _tastePrimary = Color(0xffF59E41);

TasteMapNode? _nearestNode(
  Offset position,
  Size size,
  List<TasteMapNode> nodes,
  String level,
  double scale,
  TasteMapNode? anchor,
) {
  TasteMapNode? nearest;
  var nearestDistance = double.infinity;
  for (final node in nodes) {
    final center = _expandedMapOffset(
      node.x,
      node.y,
      size,
      scale,
      anchor,
    );
    final distance = (position - center).distance;
    final hitRadius = math.max(24.0, _nodeRadius(node, size, level) + 8);
    if (distance <= hitRadius && distance < nearestDistance) {
      nearest = node;
      nearestDistance = distance;
    }
  }
  return nearest;
}

Offset _mapOffset(double x, double y, Size size) {
  final side = math.max(1.0, size.shortestSide - 76);
  return Offset(
    (size.width - side) / 2 + x.clamp(0, 1) * side,
    (size.height - side) / 2 + y.clamp(0, 1) * side,
  );
}

double _nodeRadius(TasteMapNode node, Size size, String level) {
  if (level == 'store') return 5;
  final minimum = level == 'cat2' ? 26.0 : 18.0;
  final maximum = level == 'cat2' ? 76.0 : 46.0;
  return (node.radius * size.shortestSide).clamp(minimum, maximum).toDouble();
}

Offset _expandedMapOffset(
  double x,
  double y,
  Size size,
  double scale,
  TasteMapNode? anchor,
) {
  final progress = ((scale - 1) / 3).clamp(0.0, 1.0).toDouble();
  final origin = anchor == null
      ? Offset(size.width / 2, size.height / 2)
      : _mapOffset(anchor.x, anchor.y, size);
  return origin + (_mapOffset(x, y, size) - origin) * (1 + progress * 18);
}

Offset _routeOffset(
  TasteTrailPoint point,
  Size size,
  List<TasteMapNode> nodes,
  String level,
  double scale,
  TasteMapNode? anchor,
) {
  if (anchor != null && nodes.isNotEmpty) {
    final pointOffset = _mapOffset(point.x, point.y, size);
    final node = nodes.reduce(
      (nearest, candidate) =>
          (pointOffset - _mapOffset(candidate.x, candidate.y, size)).distance <
                  (pointOffset - _mapOffset(nearest.x, nearest.y, size)).distance
              ? candidate
              : nearest,
    );
    final nodeOffset = _mapOffset(node.x, node.y, size);
    final delta = pointOffset - nodeOffset;
    final limit = math.max(0.0, _nodeRadius(node, size, level) - 20);
    final local = delta.distance <= limit || delta.distance == 0
        ? delta
        : delta / delta.distance * limit;
    return _expandedMapOffset(node.x, node.y, size, scale, anchor) + local;
  }
  TasteMapNode? category = anchor;
  for (final node in nodes) {
    if (category == null && node.name == point.region) category = node;
  }
  if (category == null) {
    return _expandedMapOffset(point.x, point.y, size, scale, null);
  }
  final baseAnchor = _mapOffset(category.x, category.y, size);
  final movedAnchor = _expandedMapOffset(
    category.x,
    category.y,
    size,
    scale,
    anchor,
  );
  return movedAnchor + (_mapOffset(point.x, point.y, size) - baseAnchor);
}

class _DetailedTasteMapPainter extends CustomPainter {
  final List<TasteMapNode> nodes;
  final String level;
  final String? category;
  final List<TasteTrailPoint> trail;
  final TasteNow? now;
  final Map<String, double> affinities;
  final double scale;
  final TasteMapNode? anchor;

  const _DetailedTasteMapPainter({
    required this.nodes,
    required this.level,
    this.category,
    required this.trail,
    required this.now,
    required this.affinities,
    required this.scale,
    this.anchor,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final visibleTrail = level == 'store'
        ? const <TasteTrailPoint>[]
        : category == null
        ? trail
        : trail.where((point) => point.region == category).toList();
    final visibleNow = level == 'store'
        ? null
        : category == null || now?.region == category
        ? now
        : null;
    final labelBounds = <Rect>[];
    for (final node in nodes) {
      final center = _expandedMapOffset(
        node.x,
        node.y,
        size,
        scale,
        anchor,
      );
      final radius = _nodeRadius(node, size, level);
      final affinity = affinities[node.key] ?? 0;
      const color = _tastePrimary;
      canvas.drawCircle(
        center,
        radius,
        Paint()..color = color.withValues(alpha: .10 + affinity * .30),
      );
      canvas.drawCircle(
        center,
        radius,
        Paint()
          ..color = color.withValues(alpha: .45 + affinity * .55)
          ..strokeWidth = 1.2 + affinity * 2.8
          ..style = PaintingStyle.stroke,
      );

      final label = TextPainter(
        text: TextSpan(
          children: [
            TextSpan(
              text: node.name,
              style: TextStyle(
                color: const Color(0xff253246),
                fontFamily: 'Paperlogy',
                fontWeight: affinity > .42 ? FontWeight.w800 : FontWeight.w600,
                fontSize: level == 'store' ? 11 : 12,
              ),
            ),
          ],
        ),
        textAlign: TextAlign.center,
        textDirection: TextDirection.ltr,
        maxLines: 2,
        ellipsis: '…',
      )..layout(maxWidth: math.max(radius * 1.75, 48));
      final position = center -
          Offset(label.width / 2, level == 'store' ? -14 : label.height / 2);
      final bounds = position & label.size;
      final showLabel = level != 'store' ||
          (scale >= 4 && !labelBounds.any((other) => other.overlaps(bounds)));
      if (showLabel) {
        label.paint(canvas, position);
        labelBounds.add(bounds.inflate(6));
      }
    }

    _paintTasteRoute(
      canvas,
      size,
      visibleTrail,
      visibleNow,
      (point) => _routeOffset(point, size, nodes, level, scale, anchor),
    );
  }

  @override
  bool shouldRepaint(covariant _DetailedTasteMapPainter oldDelegate) =>
      oldDelegate.nodes != nodes ||
      oldDelegate.level != level ||
      oldDelegate.category != category ||
      oldDelegate.trail != trail ||
      oldDelegate.now != now ||
      oldDelegate.affinities != affinities ||
      oldDelegate.scale != scale ||
      oldDelegate.anchor != anchor;
}

class _TasteMapPainter extends CustomPainter {
  final List<TasteRegion> regions;
  final List<TasteTrailPoint> trail;
  final TasteNow? now;
  final Map<String, double> affinities;

  const _TasteMapPainter({
    required this.regions,
    required this.trail,
    required this.now,
    this.affinities = const {},
  });

  Offset _offset(double x, double y, Size size) {
    const padding = 24.0;
    return Offset(
      padding + x.clamp(0, 1) * (size.width - padding * 2),
      padding + y.clamp(0, 1) * (size.height - padding * 2),
    );
  }

  @override
  void paint(Canvas canvas, Size size) {
    for (final region in regions) {
      final center = _offset(region.x, region.y, size);
      final affinity = affinities[region.name] ?? 0;
      const color = _tastePrimary;
      final radius = (region.radius * size.shortestSide)
          .clamp(24, 68)
          .toDouble();
      canvas.drawCircle(
        center,
        radius,
        Paint()
          ..color = Color.lerp(
            color.withValues(alpha: .10),
            color.withValues(alpha: .40),
            affinity,
          )!,
      );
      canvas.drawCircle(
        center,
        radius,
        Paint()
          ..color = Color.lerp(color.withValues(alpha: .45), color, affinity)!
          ..strokeWidth = 1 + affinity * 2
          ..style = PaintingStyle.stroke,
      );
      final label = TextPainter(
        text: TextSpan(
          text: region.name,
          style: TextStyle(
            color: const Color(0xff253246),
            fontFamily: 'Paperlogy',
            fontWeight: affinity > .4 ? FontWeight.w800 : FontWeight.w600,
            fontSize: 10 + affinity * 2,
          ),
        ),
        textAlign: TextAlign.center,
        textDirection: TextDirection.ltr,
        maxLines: 1,
        ellipsis: '…',
      )..layout(maxWidth: radius * 1.5);
      label.paint(canvas, center - Offset(label.width / 2, label.height / 2));
    }

    _paintTasteRoute(canvas, size, trail, now, (point) {
      return _offset(point.x, point.y, size);
    });
  }

  @override
  bool shouldRepaint(covariant _TasteMapPainter oldDelegate) =>
      oldDelegate.regions != regions ||
      oldDelegate.trail != trail ||
      oldDelegate.now != now ||
      oldDelegate.affinities != affinities;
}

void _paintTasteRoute(
  Canvas canvas,
  Size size,
  List<TasteTrailPoint> trail,
  TasteNow? now,
  Offset Function(TasteTrailPoint) offset,
) {
  const ink = Color(0xff344467);
  final points = [...trail, ?now];
  Offset pointAt(int index) => offset(points[index]);
  for (var i = 1; i < trail.length; i++) {
    final a = pointAt(i - 1);
    final b = pointAt(i);
    final recency = i / trail.length;
    canvas.drawLine(
      a,
      b,
      Paint()
        ..color = Colors.white.withValues(alpha: .8)
        ..strokeWidth = 5,
    );
    canvas.drawLine(
      a,
      b,
      Paint()
        ..color = ink.withValues(alpha: .25 + .65 * recency)
        ..strokeWidth = 2
        ..strokeCap = StrokeCap.round,
    );
    if ((b - a).distance > 24) {
      final direction = (b - a) / (b - a).distance;
      final tip = a + (b - a) * .65;
      final normal = Offset(-direction.dy, direction.dx);
      canvas.drawPath(
        Path()
          ..moveTo(tip.dx, tip.dy)
          ..lineTo(
            (tip - direction * 7 + normal * 4).dx,
            (tip - direction * 7 + normal * 4).dy,
          )
          ..lineTo(
            (tip - direction * 7 - normal * 4).dx,
            (tip - direction * 7 - normal * 4).dy,
          )
          ..close(),
        Paint()..color = ink.withValues(alpha: .35 + .65 * recency),
      );
    }
  }
  for (var i = 0; i < trail.length; i++) {
    final point = pointAt(i);
    canvas.drawCircle(point, 4, Paint()..color = Colors.white);
    canvas.drawCircle(
      point,
      2.5,
      Paint()..color = ink.withValues(alpha: .3 + .7 * (i + 1) / trail.length),
    );
  }
  if (now == null) return;
  final center = pointAt(trail.length);
  canvas.drawCircle(center, 18, Paint()..color = ink.withValues(alpha: .12));
  canvas.drawCircle(center, 14, Paint()..color = Colors.white);
  canvas.drawCircle(center, 12, Paint()..color = ink);
  final label = TextPainter(
    text: const TextSpan(
      text: '지금',
      style: TextStyle(
        fontFamily: 'Paperlogy',
        fontSize: 8,
        fontWeight: FontWeight.w700,
        color: Colors.white,
      ),
    ),
    textDirection: TextDirection.ltr,
  )..layout();
  label.paint(canvas, center - Offset(label.width / 2, label.height / 2));
}

Map<String, double> _regionAffinities(TasteMap taste) {
  final counts = <String, int>{};
  for (final point in taste.trail) {
    if (point.region.isNotEmpty) {
      counts.update(point.region, (value) => value + 1, ifAbsent: () => 1);
    }
  }
  if (counts.isEmpty) return const {};
  final maximum = counts.values.reduce(math.max);
  return counts.map((name, count) => MapEntry(name, count / maximum));
}

class _TasteMessage extends StatelessWidget {
  final String message;

  const _TasteMessage({required this.message});

  @override
  Widget build(BuildContext context) => SizedBox(
    height: 260,
    child: Center(
      child: Text(
        message,
        textAlign: TextAlign.center,
        style: const TextStyle(
          fontFamily: 'Paperlogy',
          color: Color(0xff78716C),
          height: 1.5,
        ),
      ),
    ),
  );
}

class _Stat extends StatelessWidget {
  final String label;
  final String value;

  const _Stat({required this.label, required this.value});

  @override
  Widget build(BuildContext context) => Expanded(
    child: Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        border: Border.all(color: const Color(0xffF0E6DC)),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: const TextStyle(color: Color(0xff78716C), fontSize: 12),
          ),
          const SizedBox(height: 3),
          Text(
            value,
            style: const TextStyle(
              fontFamily: 'Paperlogy',
              fontWeight: FontWeight.w700,
              fontSize: 18,
            ),
          ),
        ],
      ),
    ),
  );
}

class _Trend extends StatelessWidget {
  final TasteDrift drift;

  const _Trend({required this.drift});

  @override
  Widget build(BuildContext context) {
    final hasRegions = drift.fromRegion.isNotEmpty && drift.toRegion.isNotEmpty;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: const Color(0xffFDF3E8),
        border: Border.all(color: const Color(0xffFBDDBE)),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        children: [
          const Icon(
            Icons.trending_flat_rounded,
            size: 18,
            color: Color(0xffB4650F),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              hasRegions
                  ? '최근 ${drift.window}개 평가에서 ${drift.fromRegion} → ${drift.toRegion}'
                  : '최근 취향이 달라지고 있어요.',
              style: const TextStyle(
                color: Color(0xff8B5A2B),
                fontFamily: 'Paperlogy',
                fontSize: 12,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
