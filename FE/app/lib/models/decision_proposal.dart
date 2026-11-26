import 'package:matchuri/models/restaurant_card.dart';

class DecisionProposal {
  final bool ready;
  final String? proposalId;
  final String message;
  final List<RestaurantCard> cards;

  const DecisionProposal({
    required this.ready,
    required this.proposalId,
    required this.message,
    required this.cards,
  });

  factory DecisionProposal.fromJson(Map<String, dynamic> json) {
    return DecisionProposal(
      ready: json['ready'] as bool? ?? false,
      proposalId: json['proposal_id'] as String?,
      message: json['message'] as String? ?? '',
      cards: (json['cards'] as List<dynamic>? ?? const [])
          .map((card) => RestaurantCard.fromJson(card as Map<String, dynamic>))
          .toList(),
    );
  }
}
