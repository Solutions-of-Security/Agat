import copy
import unittest
from fractions import Fraction

from decision_runtime.artifacts import sealed
from scripts.lib.report_arithmetic import INPUT_SCHEMA, calculate, markdown, rounded


def records():
    return {'schemaVersion': INPUT_SCHEMA, 'sourceSha256': 'a' * 64, 'periods': [
        {'period': 'Июль', 'sourceId': 'A', 'requests': 100, 'processingHours': '400', 'exceptions': 8},
        {'period': 'Август', 'sourceId': 'B', 'requests': 120, 'processingHours': '360', 'exceptions': 6}]}


def exact(quantity):
    raw = quantity['exact']
    return Fraction(int(raw['numerator']), int(raw['denominator'])) if raw else None


class ReportArithmeticTest(unittest.TestCase):
    def test_regression_mean_uses_request_denominator_and_rate_uses_percentage_points(self):
        result = calculate(records()); metrics = result['metrics']
        self.assertEqual(exact(metrics['requests']['relativeChangePercent']), 20)
        self.assertEqual(exact(metrics['processingHours']['relativeChangePercent']), -10)
        self.assertEqual(exact(metrics['meanHoursPerRequest']['first']), 4)
        self.assertEqual(exact(metrics['meanHoursPerRequest']['second']), 3)
        self.assertEqual(exact(metrics['meanHoursPerRequest']['relativeChangePercent']), -25)
        self.assertEqual(exact(metrics['exceptionRatePercent']['first']), 8)
        self.assertEqual(exact(metrics['exceptionRatePercent']['second']), 5)
        self.assertEqual(exact(metrics['exceptionRatePercent']['change']), -3)
        self.assertEqual(metrics['exceptionRatePercent']['change']['unit'], 'percentage_points')
        self.assertEqual(exact(metrics['exceptionRatePercent']['relativeChangePercent']), Fraction(-75, 2))
        self.assertEqual(result['status'], 'calculated')

    def test_decimal_strings_and_repeating_fractions_do_not_use_float_or_intermediate_rounding(self):
        raw = records()
        raw['periods'][0].update(requests=3, processingHours='0.1', exceptions=1)
        raw['periods'][1].update(requests=7, processingHours='0.2', exceptions=1)
        result = calculate(raw, decimal_places=2)['metrics']
        self.assertEqual(exact(result['meanHoursPerRequest']['first']), Fraction(1, 30))
        self.assertEqual(exact(result['exceptionRatePercent']['change']), Fraction(-400, 21))
        self.assertEqual(result['exceptionRatePercent']['change']['display'], '-19.05')
        self.assertEqual(exact(result['meanHoursPerRequest']['relativeChangePercent']), Fraction(-100, 7))

    def test_rounding_is_half_even_symmetric_and_does_not_create_negative_zero(self):
        for text, places, expected in [('1.2345', 3, '1.234'), ('1.2355', 3, '1.236'), ('-1.2355', 3, '-1.236'),
                                        ('-0.0005', 3, '0.000'), ('9.9995', 3, '10.000'), ('2.5', 0, '2'),
                                        ('3.5', 0, '4'), ('-3.5', 0, '-4'), ('0.000001', 6, '0.000001')]:
            self.assertEqual(rounded(Fraction(text), places), expected)
        for value in (-1, 7, True, 1.5):
            with self.assertRaises(ValueError): rounded(Fraction(1), value)

    def test_zero_requests_produce_undefined_ratios_and_zero_base_does_not_become_infinity(self):
        raw = records(); raw['periods'][0].update(requests=0, processingHours='0', exceptions=0)
        result = calculate(raw); metrics = result['metrics']
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(metrics['meanHoursPerRequest']['first']['reason'], 'zero_requests')
        self.assertEqual(metrics['exceptionRatePercent']['change']['reason'], 'undefined_period_metric')
        self.assertEqual(metrics['requests']['relativeChangePercent']['reason'], 'zero_baseline')
        self.assertIsNone(metrics['requests']['relativeChangePercent']['exact'])
        self.assertEqual(exact(metrics['requests']['change']), 120)
        self.assertIn('не определено', markdown(result))

    def test_zero_rate_and_zero_mean_have_defined_absolute_change_but_undefined_relative_change(self):
        raw = records(); raw['periods'][0].update(processingHours='0', exceptions=0)
        result = calculate(raw)['metrics']
        self.assertEqual(exact(result['meanHoursPerRequest']['change']), 3)
        self.assertEqual(exact(result['exceptionRatePercent']['change']), 5)
        self.assertEqual(result['exceptionRatePercent']['relativeChangePercent']['reason'], 'zero_baseline')

    def test_scaling_population_hours_and_exceptions_preserves_means_rates_and_relative_changes(self):
        raw = records(); scaled = copy.deepcopy(raw)
        for row in scaled['periods']:
            row['requests'] *= 7; row['exceptions'] *= 7; row['processingHours'] = str(int(row['processingHours']) * 7)
        first, second = calculate(raw)['metrics'], calculate(scaled)['metrics']
        for key in ('meanHoursPerRequest', 'exceptionRatePercent'):
            self.assertEqual(first[key], second[key])
        self.assertEqual(first['requests']['relativeChangePercent'], second['requests']['relativeChangePercent'])

    def test_reversing_periods_reverses_absolute_changes_and_uses_new_relative_base(self):
        raw = records(); swapped = {**raw, 'periods': list(reversed(raw['periods']))}
        first, second = calculate(raw)['metrics'], calculate(swapped)['metrics']
        for key in first:
            self.assertEqual(exact(first[key]['change']), -exact(second[key]['change']))
        self.assertEqual(exact(second['meanHoursPerRequest']['relativeChangePercent']), Fraction(100, 3))

    def test_invalid_counts_decimal_formats_consistency_and_bounds_are_rejected(self):
        for field, invalids in [('requests', [True, -1, 1.0, '100', 1_000_000_001]),
                                ('exceptions', [True, -1, 101, 1.0, None]),
                                ('processingHours', [400, 0.1, '1e3', 'NaN', '-1', '1,5', '.5', '01', '0.1234567',
                                                     '1000000000000.1', '1' * 5000])]:
            for value in invalids:
                with self.subTest(field=field, value=str(value)[:30]), self.assertRaises(ValueError):
                    raw = records(); raw['periods'][0][field] = value; calculate(raw)
        raw = records(); raw['periods'][0].update(requests=0, exceptions=0)
        with self.assertRaises(ValueError): calculate(raw)

    def test_unknown_fields_duplicate_periods_and_markup_labels_are_rejected(self):
        for field, value in [('period', 'Июль\n[extra]'), ('period', '<script>'), ('period', 'Июль | подмена'),
                             ('sourceId', '../private'), ('sourceId', 'A]\n# injected'), ('confidence', 1)]:
            raw = records(); raw['periods'][0][field] = value
            with self.assertRaises(ValueError): calculate(raw)
        raw = records(); raw['periods'][1]['period'] = raw['periods'][0]['period']
        with self.assertRaises(ValueError): calculate(raw)
        for value in ('', 'A' * 64, '0' * 63, None):
            raw = records(); raw['sourceSha256'] = value
            with self.assertRaises(ValueError): calculate(raw)

    def test_input_is_unchanged_binding_changes_and_source_truth_is_never_asserted(self):
        raw = records(); saved = copy.deepcopy(raw); result = calculate(raw)
        self.assertEqual(raw, saved)
        self.assertFalse(result['sourceValuesVerified']); self.assertFalse(result['routingEnabled'])
        self.assertFalse(result['qualifiedForRouting']); self.assertNotIn('confidence', result)
        raw['periods'][0]['processingHours'] = '401'
        self.assertNotEqual(result['inputSha256'], calculate(raw)['inputSha256'])
        self.assertEqual(result['input'], saved)

    def test_markdown_contains_correct_units_and_rejects_even_resealed_arithmetic_tampering(self):
        result = calculate(records()); text = markdown(result)
        self.assertIn('| Число заявок | 100 | 120 | +20.000% |', text)
        self.assertIn('| Среднее время на заявку | 4.000 ч | 3.000 ч | -25.000% |', text)
        self.assertIn('| Доля отклонений | 8.000% | 5.000% | -3.000 п.п. |', text)
        result.pop('sha256'); result['metrics']['meanHoursPerRequest']['relativeChangePercent']['display'] = '-10.000'
        with self.assertRaises(ValueError): markdown(sealed(result))


if __name__ == '__main__': unittest.main()
