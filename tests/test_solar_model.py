import unittest

from solar_model import solar_offset


class SolarModelTests(unittest.TestCase):
    def test_partial_offset_separates_usage_and_export(self):
        result = solar_offset(20, 10, 60, 22.58, 5)
        self.assertEqual(result['self_consumed_kwh'], 6)
        self.assertEqual(result['remaining_grid_kwh'], 14)
        self.assertAlmostEqual(result['avoided_usage_cost'], 1.3548)
        self.assertAlmostEqual(result['export_credit'], 0.2)
        self.assertEqual(result['offset_pct'], 30)

    def test_surplus_cannot_avoid_more_than_consumption(self):
        result = solar_offset(10, 30, 100, 20)
        self.assertEqual(result['self_consumed_kwh'], 10)
        self.assertEqual(result['exported_kwh'], 20)
        self.assertEqual(result['avoided_usage_cost'], 2)
        self.assertIsNone(result['export_credit'])
        self.assertIsNone(result['total_value'])

    def test_explicit_zero_export_rate_is_not_unknown(self):
        result = solar_offset(10, 30, 100, 20, 0)
        self.assertEqual(result['export_credit'], 0)
        self.assertEqual(result['total_value'], 2)

    def test_zero_generation_and_zero_demand(self):
        self.assertEqual(solar_offset(10, 0, 100, 20)['total_value'], 0)
        result = solar_offset(0, 10, 100, 20)
        self.assertEqual(result['avoided_usage_cost'], 0)
        self.assertIsNone(result['offset_pct'])
        self.assertIsNone(result['export_credit'])

    def test_missing_nonfinite_negative_and_invalid_percent_are_rejected(self):
        for value in (None, True, '10', float('nan'), float('inf'), -1, 1_000_001, 10**1000):
            for index in (0, 1, 3, 4):
                if value is None and index == 4:
                    continue  # An unknown export rate is explicitly supported.
                args = [20, 10, 50, 22.58, 5]
                args[index] = value
                with self.subTest(value=value, index=index), self.assertRaises(ValueError):
                    solar_offset(*args)
        with self.assertRaises(ValueError):
            solar_offset(20, 10, 101, 22.58)
