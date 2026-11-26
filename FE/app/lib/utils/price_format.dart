String formatPrice(int price) {
  final digits = price.toString();
  final buffer = StringBuffer();
  for (int i = 0; i < digits.length; i++) {
    final fromEnd = digits.length - i;
    buffer.write(digits[i]);
    if (fromEnd > 1 && fromEnd % 3 == 1) buffer.write(',');
  }
  return buffer.toString();
}
