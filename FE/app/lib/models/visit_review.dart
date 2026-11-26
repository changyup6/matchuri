class VisitReviewTarget {
  final String userId;
  final int cardId;
  final String storeName;
  final String visitId;
  final bool simulated;
  final bool? arrived;

  const VisitReviewTarget({
    required this.userId,
    required this.cardId,
    required this.storeName,
    required this.visitId,
    this.simulated = false,
    this.arrived,
  });

  factory VisitReviewTarget.fromJson(Map<String, dynamic> json) =>
      VisitReviewTarget(
        userId: json['userId'] as String,
        cardId: (json['cardId'] as num).toInt(),
        storeName: json['storeName'] as String,
        visitId: json['visitId'] as String,
        simulated: json['simulated'] == true,
        arrived: json['arrived'] as bool?,
      );
}
