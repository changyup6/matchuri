import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:matchuri/models/decision_proposal.dart';
import 'package:matchuri/models/restaurant_card.dart';

class DecisionProposalResult {
  final RestaurantCard? selectedCard;
  final bool resetSession;

  const DecisionProposalResult.select(RestaurantCard card)
      : selectedCard = card,
        resetSession = false;

  const DecisionProposalResult.reset()
      : selectedCard = null,
        resetSession = true;
}

Future<DecisionProposalResult?> showDecisionProposalDialog(
  BuildContext context,
  DecisionProposal proposal,
) {
  return showDialog<DecisionProposalResult>(
    context: context,
    barrierDismissible: false,
    builder: (_) => _DecisionProposalDialog(proposal: proposal),
  );
}

class _DecisionProposalDialog extends StatelessWidget {
  final DecisionProposal proposal;

  const _DecisionProposalDialog({required this.proposal});

  @override
  Widget build(BuildContext context) {
    final gridHeight =
        (MediaQuery.sizeOf(context).height * 0.46).clamp(280.0, 440.0).toDouble();
    return Dialog(
      insetPadding: const EdgeInsets.all(20),
      backgroundColor: Colors.white,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(22)),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(18, 20, 18, 14),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              children: [
                const Expanded(
                  child: Text(
                    '당신을 위한 추천 맛집',
                    style: TextStyle(
                      fontFamily: 'Paperlogy',
                      fontWeight: FontWeight.w800,
                      fontSize: 20,
                    ),
                  ),
                ),
                IconButton(
                  onPressed: () => Navigator.of(context).pop(),
                  icon: const Icon(Icons.close),
                  tooltip: '닫기',
                ),
              ],
            ),
            const SizedBox(height: 6),
            Text(
              proposal.message,
              textAlign: TextAlign.center,
              style: const TextStyle(
                color: Color(0xFF6B7280),
                fontFamily: 'Paperlogy',
                fontSize: 12,
                height: 1.4,
              ),
            ),
            const SizedBox(height: 16),
            SizedBox(
              height: gridHeight,
              child: GridView.builder(
                physics: const NeverScrollableScrollPhysics(),
                itemCount: proposal.cards.length,
                gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                  crossAxisCount: 2,
                  mainAxisSpacing: 12,
                  crossAxisSpacing: 12,
                  childAspectRatio: 0.83,
                ),
                itemBuilder: (context, index) => _ProposalCard(
                  card: proposal.cards[index],
                  onSelected: () => Navigator.of(context).pop(
                    DecisionProposalResult.select(proposal.cards[index]),
                  ),
                ),
              ),
            ),
            const SizedBox(height: 16),
            SizedBox(
              width: double.infinity,
              child: FilledButton(
                onPressed: () => Navigator.of(context).pop(
                  const DecisionProposalResult.reset(),
                ),
                style: FilledButton.styleFrom(
                  backgroundColor: const Color(0xFFF9A13A),
                  minimumSize: const Size.fromHeight(50),
                ),
                child: const Text('다시 추천받기'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ProposalCard extends StatelessWidget {
  final RestaurantCard card;
  final VoidCallback onSelected;

  const _ProposalCard({required this.card, required this.onSelected});

  @override
  Widget build(BuildContext context) {
    final image = card.image;
    return Container(
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(16),
        color: const Color(0xFFF3F4F6),
      ),
      child: Stack(
        fit: StackFit.expand,
        children: [
          if (image != null)
            CachedNetworkImage(
              imageUrl: image,
              fit: BoxFit.cover,
              placeholder: (_, _) => const ColoredBox(color: Color(0xFFE5E7EB)),
              errorWidget: (_, _, _) => const ColoredBox(color: Color(0xFFE5E7EB)),
            ),
          DecoratedBox(
            decoration: BoxDecoration(
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.bottomCenter,
                colors: [Colors.black.withValues(alpha: 0.08), Colors.black54],
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  card.store.name,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: Colors.white,
                    fontFamily: 'Paperlogy',
                    fontWeight: FontWeight.w800,
                    fontSize: 16,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  card.store.category,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: Colors.white70,
                    fontFamily: 'Paperlogy',
                    fontSize: 12,
                  ),
                ),
                const Spacer(),
                SizedBox(
                  width: double.infinity,
                  child: FilledButton(
                    onPressed: onSelected,
                    style: FilledButton.styleFrom(
                      backgroundColor: const Color(0xFFF9A13A),
                      minimumSize: const Size.fromHeight(38),
                      padding: EdgeInsets.zero,
                    ),
                    child: const Text('식당 선택하기'),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
