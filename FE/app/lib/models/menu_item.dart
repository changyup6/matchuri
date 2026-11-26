class MenuItem {
  final int id;
  final String storeId;
  final String name;
  final int? price;
  final String? image;

  MenuItem({
    required this.id,
    required this.storeId,
    required this.name,
    required this.price,
    this.image,
  });

  factory MenuItem.fromJson(Map<String, dynamic> json) {
    return MenuItem(
      id: json['id'] as int,
      storeId: json['store_id'] as String,
      name: json['name'] as String,
      price: (json['price'] as num?)?.toInt(),
      image: json['image'] as String?,
    );
  }
}
