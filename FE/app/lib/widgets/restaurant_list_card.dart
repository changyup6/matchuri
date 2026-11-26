import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:matchuri/models/restaurant_card.dart';
import 'package:matchuri/widgets/tag_chip.dart';

class RestaurantListCard extends StatelessWidget {
  final RestaurantCard card;
  final VoidCallback? onTap;
  final VoidCallback? onRemove;

  const RestaurantListCard({
    super.key,
    required this.card,
    this.onTap,
    this.onRemove,
  });

  @override
  Widget build(BuildContext context) {
    final store = card.store;
    final cacheWidth =
        (MediaQuery.sizeOf(context).width *
                MediaQuery.devicePixelRatioOf(context))
            .round();

    return GestureDetector(
      onTap: onTap,
      child: Container(
        margin: const EdgeInsets.only(bottom: 16),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: const Color(0xffE5E7EB)),
        ),
        clipBehavior: Clip.antiAlias,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(
              height: 150,
              width: double.infinity,
              child: Stack(
                fit: StackFit.expand,
                children: [
                  card.image != null
                      ? CachedNetworkImage(
                          imageUrl: card.image!,
                          fit: BoxFit.cover,
                          memCacheWidth: cacheWidth,
                          fadeInDuration: const Duration(milliseconds: 150),
                          placeholder: (context, url) =>
                              Container(color: Colors.grey.shade200),
                          errorWidget: (context, url, error) =>
                              Container(color: Colors.grey.shade300),
                        )
                      : Container(color: Colors.grey.shade300),
                  if (onRemove != null)
                    Positioned(
                      top: 8,
                      right: 8,
                      child: Semantics(
                        button: true,
                        label: '북마크 제거',
                        child: IconButton(
                          key: ValueKey('bookmark-${card.cardId}'),
                          onPressed: onRemove,
                          tooltip: '북마크 제거',
                          style: IconButton.styleFrom(
                            minimumSize: const Size(48, 48),
                            backgroundColor: Colors.white.withValues(alpha: .9),
                          ),
                          icon: SvgPicture.asset(
                            'assets/icons/bookmark_color.svg',
                            width: 24,
                            height: 24,
                          ),
                        ),
                      ),
                    ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(
                        child: Text(
                          store.name,
                          style: const TextStyle(
                            fontFamily: 'Paperlogy',
                            fontWeight: FontWeight.w800,
                            fontSize: 17,
                            color: Colors.black,
                          ),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      if (store.score != null) ...[
                        const SizedBox(width: 8),
                        const Icon(Icons.star, size: 15, color: Colors.amber),
                        const SizedBox(width: 2),
                        Transform.translate(
                          offset: const Offset(0, -2),
                          child: Text(
                            store.score!.toStringAsFixed(1),
                            style: const TextStyle(
                              fontFamily: 'Paperlogy',
                              fontWeight: FontWeight.w600,
                              fontSize: 13,
                            ),
                          ),
                        ),
                      ],
                    ],
                  ),
                  const SizedBox(height: 2),
                  Text(
                    [
                      store.category,
                      if (store.subway != null) store.subway,
                    ].join('  ·  '),
                    style: TextStyle(
                      fontFamily: 'Paperlogy',
                      fontWeight: FontWeight.w500,
                      fontSize: 12,
                      color: Colors.grey.shade500,
                    ),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  if (store.summary != null) ...[
                    const SizedBox(height: 8),
                    Text(
                      store.summary!,
                      style: const TextStyle(
                        fontFamily: 'Paperlogy',
                        fontWeight: FontWeight.w500,
                        fontSize: 13,
                        color: Color(0xff4B5563),
                      ),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                  if (store.tags.isNotEmpty) ...[
                    const SizedBox(height: 10),
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: store.tags
                          .map((tag) => TagChip(text: tag))
                          .toList(),
                    ),
                  ],
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
