import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:matchuri/models/menu_item.dart';
import 'package:matchuri/utils/price_format.dart';

class MenuTile extends StatelessWidget {
  final MenuItem menu;
  final double size;
  final VoidCallback? onPhotoTap;

  const MenuTile({
    super.key,
    required this.menu,
    this.size = 96,
    this.onPhotoTap,
  });

  @override
  Widget build(BuildContext context) {
    final decodeSize = (size * MediaQuery.devicePixelRatioOf(context)).round();

    return SizedBox(
      width: size,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Semantics(
            button: menu.image != null && onPhotoTap != null,
            label: '${menu.name} 사진 크게 보기',
            child: GestureDetector(
              onTap: menu.image != null ? onPhotoTap : null,
              child: ClipRRect(
                borderRadius: BorderRadius.circular(12),
                child: SizedBox(
                  width: size,
                  height: size,
                  child: menu.image == null
                      ? const _MenuImageState.empty()
                      : CachedNetworkImage(
                          imageUrl: menu.image!,
                          fit: BoxFit.cover,
                          memCacheWidth: decodeSize,
                          memCacheHeight: decodeSize,
                          fadeInDuration: const Duration(milliseconds: 150),
                          placeholder: (context, url) =>
                              const _MenuImageState.loading(),
                          errorWidget: (context, url, error) =>
                              const _MenuImageState.error(),
                        ),
                ),
              ),
            ),
          ),
          const SizedBox(height: 6),
          Text(
            menu.name,
            style: const TextStyle(
              color: Colors.black87,
              fontSize: 13,
              fontWeight: FontWeight.w600,
            ),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          Text(
            menu.price != null ? '${formatPrice(menu.price!)}원' : '가격 문의',
            style: const TextStyle(color: Colors.black54, fontSize: 12),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
    );
  }
}

enum _MenuImageKind { empty, loading, error }

class _MenuImageState extends StatelessWidget {
  final _MenuImageKind kind;

  const _MenuImageState.empty() : kind = _MenuImageKind.empty;
  const _MenuImageState.loading() : kind = _MenuImageKind.loading;
  const _MenuImageState.error() : kind = _MenuImageKind.error;

  @override
  Widget build(BuildContext context) {
    switch (kind) {
      case _MenuImageKind.empty:
        return Container(
          color: Colors.grey.shade200,
          alignment: Alignment.center,
          child: Icon(Icons.restaurant, color: Colors.grey.shade400, size: 28),
        );
      case _MenuImageKind.loading:
        return Container(
          color: Colors.grey.shade100,
          alignment: Alignment.center,
          child: const SizedBox(
            width: 18,
            height: 18,
            child: CircularProgressIndicator(
              strokeWidth: 2,
              color: Colors.black26,
            ),
          ),
        );
      case _MenuImageKind.error:
        return Container(
          color: const Color(0xffFEE2E2),
          alignment: Alignment.center,
          child: Icon(
            Icons.broken_image_outlined,
            color: Colors.red.shade300,
            size: 26,
          ),
        );
    }
  }
}
