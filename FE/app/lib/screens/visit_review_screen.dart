import 'package:flutter/material.dart';
import 'package:matchuri/models/visit_review.dart';
import 'package:matchuri/services/survey_api.dart';
import 'package:matchuri/widgets/primary_button.dart';

class VisitReviewScreen extends StatefulWidget {
  final VisitReviewTarget target;
  final SurveyApi? api;
  const VisitReviewScreen({super.key, required this.target, this.api});

  @override
  State<VisitReviewScreen> createState() => _VisitReviewScreenState();
}

class _VisitReviewScreenState extends State<VisitReviewScreen> {
  final _form = GlobalKey<FormState>();
  final _taste = TextEditingController();
  final _price = TextEditingController();
  final _mood = TextEditingController();
  String _reviewTopic = 'taste';
  late final SurveyApi _api = widget.api ?? SurveyApi();
  bool? _visited;
  String? _satisfaction;
  String? _reason;
  String? _error;
  bool _saving = false;
  bool _done = false;
  bool _recorded = false;
  static const _orange = Color(0xffF59E41);

  @override
  void dispose() {
    _taste.dispose();
    _price.dispose();
    _mood.dispose();
    if (widget.api == null) _api.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_saving || _done) return;
    if (_visited == null ||
        (_visited! &&
            (_satisfaction == null ||
                [_taste, _price, _mood].any((controller) => controller.text.trim().isEmpty))) ||
        (!_visited! && _reason == null)) {
      setState(() => _error = '방문 정보와 맛, 가격, 분위기 리뷰를 모두 남겨 주세요.');
      return;
    }
    if (!_form.currentState!.validate()) return;
    setState(() { _saving = true; _error = null; });
    try {
      final recorded = await _api.submit(
        userId: widget.target.userId,
        cardId: widget.target.cardId,
        visited: _visited!,
        satisfaction: _satisfaction,
        taste: _taste.text,
        price: _price.text,
        mood: _mood.text,
        notVisitedReason: _reason,
        // Simulated notifications provide no evidence of a physical visit.
        arrived: widget.target.simulated ? null : widget.target.arrived,
      );
      if (!mounted) return;
      setState(() { _done = true; _recorded = recorded; });
    } catch (error) {
      if (mounted) {
        setState(() => _error = error is SurveyApiException
            ? error.toString() : '저장하지 못했어요. 연결을 확인하고 다시 시도해 주세요.');
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Widget _title(String value) => Padding(
    padding: const EdgeInsets.only(top: 26, bottom: 12),
    child: Text(value, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700)),
  );

  Widget _choice(String label, bool selected, VoidCallback select) => ChoiceChip(
    label: Text(label),
    selected: selected,
    onSelected: _saving ? null : (_) => setState(select),
    selectedColor: const Color(0xffFFF0DE),
    backgroundColor: const Color(0xffF7F7F7),
    side: BorderSide(color: selected ? _orange : const Color(0xffE5E5E5)),
    labelStyle: TextStyle(color: selected ? const Color(0xff9A5519) : const Color(0xff555555)),
    showCheckmark: false,
    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 10),
  );

  Widget _reviewField() {
    final (label, hint, controller) = switch (_reviewTopic) {
      'price' => ('가격', '가격과 양은 어땠나요?', _price),
      'mood' => ('분위기', '공간과 분위기는 어땠나요?', _mood),
      _ => ('맛', '어떤 메뉴가 기억에 남았나요?', _taste),
    };
    return TextFormField(
      controller: controller,
      enabled: !_saving,
      minLines: 4,
      maxLines: 5,
      maxLength: 200,
      decoration: InputDecoration(
        labelText: label,
        hintText: hint,
        alignLabelWithHint: true,
        filled: true,
        fillColor: const Color(0xffFFFAF4),
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: Color(0xffE8DFD3))),
        focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: const BorderSide(color: _orange, width: 2)),
      ),
      validator: (value) => value == null || value.trim().isEmpty ? '$label 이야기를 한 줄 남겨 주세요.' : null,
    );
  }

  @override
  Widget build(BuildContext context) => Theme(
    data: Theme.of(context).copyWith(
      colorScheme: ColorScheme.fromSeed(seedColor: _orange),
      textTheme: Theme.of(context).textTheme.apply(fontFamily: 'Paperlogy'),
    ),
    child: PopScope(
      canPop: !_saving,
      child: Scaffold(
        backgroundColor: Colors.white,
        appBar: AppBar(
          title: const Text('방문 이야기', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
          centerTitle: false, titleSpacing: 0,
          backgroundColor: Colors.white, surfaceTintColor: Colors.white,
        ),
        body: SafeArea(
          child: _done ? Center(
            child: Padding(
              padding: const EdgeInsets.all(28),
              child: Column(mainAxisSize: MainAxisSize.min, children: [
                const Icon(Icons.check_circle_rounded, color: _orange, size: 64),
                const SizedBox(height: 20),
                Text(_recorded ? '이야기를 남겨주셔서 고마워요!' : '이미 답변한 방문이에요.',
                  textAlign: TextAlign.center, style: const TextStyle(fontSize: 21, fontWeight: FontWeight.w700)),
                const SizedBox(height: 24),
                PrimaryButton(label: '완료', onPressed: () => Navigator.of(context).pop()),
              ]),
            ),
          ) : Form(
            key: _form,
            child: ListView(
              padding: const EdgeInsets.fromLTRB(24, 20, 24, 32),
              children: [
                Container(
                  padding: const EdgeInsets.all(20),
                  decoration: BoxDecoration(color: const Color(0xffFFF4E6), borderRadius: BorderRadius.circular(20)),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    const Icon(Icons.restaurant_rounded, color: _orange, size: 32),
                    const SizedBox(height: 12),
                    Text(widget.target.storeName, style: const TextStyle(fontSize: 23, fontWeight: FontWeight.w800)),
                    const SizedBox(height: 8),
                    const Text('직접 다녀온 이야기를 들려주세요.', style: TextStyle(color: Color(0xff806348), height: 1.5)),
                  ]),
                ),
                if (widget.target.simulated) const Padding(
                  padding: EdgeInsets.only(top: 12),
                  child: Text('테스트 알림으로 열었어요. 제출한 리뷰는 실제로 저장됩니다.',
                    style: TextStyle(fontSize: 12, color: Color(0xff856541), height: 1.5)),
                ),
                _title('이 식당에 방문하셨나요?'),
                Wrap(spacing: 8, runSpacing: 8, children: [
                  _choice('네, 다녀왔어요', _visited == true, () => _visited = true),
                  _choice('방문하지 않았어요', _visited == false, () => _visited = false),
                ]),
                if (_visited == true) ...[
                  _title('추천이 취향에 맞았나요?'),
                  Wrap(spacing: 8, runSpacing: 8, children: [
                    for (final item in {'good': '좋았어요', 'soso': '보통이에요', 'bad': '아쉬웠어요'}.entries)
                      _choice(item.value, _satisfaction == item.key, () => _satisfaction = item.key),
                  ]),
                  _title('리뷰를 남겨주세요'),
                  const Padding(
                    padding: EdgeInsets.only(bottom: 12),
                    child: Text('항목을 바꿔도 작성한 내용은 유지돼요.', style: TextStyle(fontSize: 12, color: Colors.grey)),
                  ),
                  Wrap(
                    spacing: 8,
                    children: [
                      _choice('맛', _reviewTopic == 'taste', () => _reviewTopic = 'taste'),
                      _choice('가격', _reviewTopic == 'price', () => _reviewTopic = 'price'),
                      _choice('분위기', _reviewTopic == 'mood', () => _reviewTopic = 'mood'),
                    ],
                  ),
                  const SizedBox(height: 12),
                  _reviewField(),
                ],
                if (_visited == false) ...[
                  _title('방문하지 않은 이유가 있나요?'),
                  Wrap(spacing: 8, runSpacing: 8, children: [
                    for (final item in {'far': '거리가 멀어요', 'no_time': '시간이 없었어요', 'changed_mind': '마음이 바뀌었어요', 'went_elsewhere': '다른 곳에 갔어요'}.entries)
                      _choice(item.value, _reason == item.key, () => _reason = item.key),
                  ]),
                ],
                if (_error != null) Padding(padding: const EdgeInsets.only(top: 16),
                  child: Text(_error!, style: const TextStyle(color: Color(0xffB3261E)))),
                const SizedBox(height: 28),
                PrimaryButton(label: '이야기 남기기', isLoading: _saving, onPressed: _saving ? null : _submit),
              ],
            ),
          ),
        ),
      ),
    ),
  );
}
