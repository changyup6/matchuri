import 'package:flutter/material.dart';
import 'package:matchuri/models/app_user.dart';
import 'package:matchuri/services/location_service.dart';
import 'package:matchuri/services/preferences_api.dart';
import 'package:matchuri/widgets/primary_button.dart';

const _categoryIcons = {
  '한식': 'korean',
  '카페': 'cafe',
  '일식': 'japan',
  '술집': 'alcohol',
  '양식': 'western',
  '중식': 'chinese',
  '간식': 'dessert',
  '분식': 'bunsik',
  '기타': 'etc',
};

class FilterScreen extends StatefulWidget {
  final String userId;
  final String sessionId;
  final PreferencesApi userService;
  final Future<void> Function(UserFilter filter) onCompleted;
  final UserFilter? initialFilter;

  const FilterScreen({
    super.key,
    required this.userId,
    required this.sessionId,
    required this.userService,
    required this.onCompleted,
    this.initialFilter,
  });

  @override
  State<FilterScreen> createState() => _FilterScreenState();
}

class _FilterScreenState extends State<FilterScreen> {
  final _selectedCategories = <String>{};
  final _selectedTags = <String>{};
  final _locationService = LocationService();
  List<String> _categories = [];
  List<String> _tags = [];
  int? _withinMeters = 500;
  String? _error;
  bool _loading = true;
  bool _submitting = false;

  @override
  void initState() {
    super.initState();
    final filter = widget.initialFilter;
    if (filter != null) {
      _selectedCategories.addAll(filter.categories);
      _selectedTags.addAll(filter.tags);
      _withinMeters = filter.withinMeters;
    }
    _loadOptions();
  }

  Future<void> _loadOptions() async {
    try {
      final options = await widget.userService.loadFilterOptions();
      if (!mounted) return;
      setState(() {
        _categories = options.categories;
        _tags = options.tags;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = '필터 목록을 불러오지 못했어요.';
        _loading = false;
      });
    }
  }

  Future<void> _completeFilters() async {
    if (_submitting) return;
    setState(() {
      _submitting = true;
      _error = null;
    });

    try {
      final location = await _locationService.getCurrentLocation();
      if (location == null) {
        if (mounted) {
          setState(() {
            _submitting = false;
            _error = '위치 권한을 허용한 뒤 다시 시도해 주세요.';
          });
        }
        return;
      }
      // A saved filter is sufficient to enter the app.  Location is refreshed
      // again while the user swipes, so a transient location API failure must
      // not be reported as a filter-save failure.
      await widget.userService.saveFilters(
        userId: widget.userId,
        sessionId: widget.sessionId,
        categories: _selectedCategories,
        tags: _selectedTags,
        withinMeters: _withinMeters,
      );
      try {
        await widget.userService.saveLocation(
          userId: widget.userId,
          sessionId: widget.sessionId,
          latitude: location.lat,
          longitude: location.lng,
        );
      } catch (error) {
        debugPrint('현재 위치 저장 실패: $error');
      }
      if (mounted) {
        await widget.onCompleted(
          UserFilter(
            categories: _selectedCategories.toList(),
            tags: _selectedTags.toList(),
            withinMeters: _withinMeters,
          ),
        );
      }
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _submitting = false;
        _error = '필터 저장에 실패했어요. 다시 시도해 주세요.';
      });
    }
  }

  void _toggle(Set<String> selected, String value) => setState(() {
    if (!selected.add(value)) selected.remove(value);
  });

  String _distanceLabel(int meters) {
    if (meters < 1000) return '${meters}m';
    final kilometers = meters / 1000;
    return '${kilometers.toStringAsFixed(kilometers % 1 == 0 ? 0 : 1)}km';
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    backgroundColor: Colors.white,
    body: SafeArea(
      child: _loading
          ? const Center(child: CircularProgressIndicator())
          : Column(
              children: [
                Expanded(
                  child: SingleChildScrollView(
                    padding: const EdgeInsets.fromLTRB(40, 30, 40, 16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const _SectionTitle('먹고 싶은 음식'),
                        const SizedBox(height: 20),
                        _ChoiceGrid(
                          values: _categories,
                          selected: _selectedCategories,
                          icons: _categoryIcons,
                          onTap: (value) => _toggle(_selectedCategories, value),
                        ),
                        const SizedBox(height: 36),
                        Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            const _SectionTitle('거리'),
                            Row(
                              children: [
                                Semantics(
                                  button: true,
                                  selected: _withinMeters == null,
                                  label: '거리 무관',
                                  child: InkWell(
                                    onTap: () => setState(
                                      () => _withinMeters =
                                          _withinMeters == null ? 500 : null,
                                    ),
                                    borderRadius: BorderRadius.circular(18),
                                    child: Ink(
                                      padding: const EdgeInsets.symmetric(
                                        horizontal: 12,
                                        vertical: 8,
                                      ),
                                      decoration: BoxDecoration(
                                        color: _withinMeters == null
                                            ? const Color(0xFFFFF7EE)
                                            : const Color(0xFFF5F6F8),
                                        border: Border.all(
                                          color: _withinMeters == null
                                              ? const Color(0xFFFFD2A3)
                                              : const Color(0xFFD5D8DE),
                                          width: 1.5,
                                        ),
                                        borderRadius: BorderRadius.circular(18),
                                      ),
                                      child: Text(
                                        '거리 무관',
                                        style: TextStyle(
                                          color: _withinMeters == null
                                              ? const Color(0xFFF59E41)
                                              : const Color(0xFF9BA1AB),
                                          fontFamily: 'Paperlogy',
                                          fontWeight: FontWeight.w700,
                                          fontSize: 13,
                                        ),
                                      ),
                                    ),
                                  ),
                                ),
                                const SizedBox(width: 8),
                                Text(
                                  _withinMeters == null
                                      ? '거리 무관'
                                      : '${_distanceLabel(_withinMeters!)} 이내',
                                  style: const TextStyle(
                                    color: Color(0xFF878787),
                                    fontFamily: 'Paperlogy',
                                    fontWeight: FontWeight.w700,
                                    fontSize: 15,
                                  ),
                                ),
                              ],
                            ),
                          ],
                        ),
                        Slider(
                          value: (_withinMeters ?? 500).toDouble(),
                          min: 500,
                          max: 5000,
                          divisions: 9,
                          padding: EdgeInsets.zero,
                          activeColor: const Color(0xFFF59E41),
                          inactiveColor: const Color(0xFFE5E5E5),
                          onChanged: _withinMeters == null
                              ? null
                              : (value) => setState(
                                  () => _withinMeters = value.round(),
                                ),
                        ),
                        const SizedBox(height: 28),
                        const _SectionTitle('유형'),
                        const SizedBox(height: 20),
                        _ChoiceGrid(
                          values: _tags,
                          selected: _selectedTags,
                          prefix: '# ',
                          onTap: (value) => _toggle(_selectedTags, value),
                        ),
                      ],
                    ),
                  ),
                ),
                if (_error != null)
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 40),
                    child: Text(
                      _error!,
                      style: const TextStyle(color: Colors.red),
                    ),
                  ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(40, 16, 40, 24),
                  child: PrimaryButton(
                    label: '추천받으러 가기',
                    onPressed: _loading || _submitting
                        ? null
                        : _completeFilters,
                    isLoading: _submitting,
                  ),
                ),
              ],
            ),
    ),
  );
}

class _SectionTitle extends StatelessWidget {
  final String text;

  const _SectionTitle(this.text);

  @override
  Widget build(BuildContext context) => Text(
    text,
    style: const TextStyle(
      fontFamily: 'Paperlogy',
      fontWeight: FontWeight.w700,
      fontSize: 24,
    ),
  );
}

class _ChoiceGrid extends StatelessWidget {
  final List<String> values;
  final Set<String> selected;
  final Map<String, String>? icons;
  final String prefix;
  final ValueChanged<String> onTap;

  const _ChoiceGrid({
    required this.values,
    required this.selected,
    this.icons,
    this.prefix = '',
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) => LayoutBuilder(
    builder: (context, constraints) {
      final columns = constraints.maxWidth < 270 ? 2 : 3;
      return GridView.count(
        crossAxisCount: columns,
        crossAxisSpacing: 10,
        mainAxisSpacing: 12,
        childAspectRatio: columns == 2 ? 2.8 : 2.5,
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        children: values
            .map(
              (value) => _FilterChip(
                label: '$prefix$value',
                selected: selected.contains(value),
                icon: icons?[value],
                onTap: () => onTap(value),
              ),
            )
            .toList(),
      );
    },
  );
}

class _FilterChip extends StatelessWidget {
  final String label;
  final bool selected;
  final String? icon;
  final VoidCallback onTap;

  const _FilterChip({
    required this.label,
    required this.selected,
    this.icon,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) => Semantics(
    button: true,
    selected: selected,
    label: label,
    child: InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(20),
      child: Ink(
        decoration: BoxDecoration(
          color: selected ? const Color(0xFFFFF7EE) : const Color(0xFFF5F6F8),
          border: Border.all(
            color: selected ? const Color(0xFFFFD2A3) : const Color(0xFFD5D8DE),
            width: 1.5,
          ),
          borderRadius: BorderRadius.circular(20),
        ),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            if (icon != null) ...[
              Image.asset(
                'assets/icons/$icon${selected ? '_color' : ''}.png',
                width: 20,
                height: 20,
              ),
              const SizedBox(width: 5),
            ],
            Flexible(
              child: Text(
                label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  color: selected
                      ? const Color(0xFFF59E41)
                      : const Color(0xFF9BA1AB),
                  fontFamily: 'Paperlogy',
                  fontWeight: FontWeight.w700,
                  fontSize: 14,
                ),
              ),
            ),
          ],
        ),
      ),
    ),
  );
}
