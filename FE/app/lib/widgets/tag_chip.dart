import 'package:flutter/material.dart';

/// 가게·리뷰 태그를 표시하는 알약 모양 칩.
class TagChip extends StatelessWidget {
  final String text;

  const TagChip({super.key, required this.text});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: const Color(0xffF59E41).withValues(alpha: 0.12),
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
