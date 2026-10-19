import unittest

import web


class UsageRecommendationsTests(unittest.TestCase):
    def test_ranks_top_five_and_excludes_empty_loads(self):
        rows = [
            {"channel_name": str(i), "total_kwh": i}
            for i in range(8)
        ]
        result = web._usage_cost_recommendations(rows, 0.2258)
        self.assertEqual(len(result), 5)
        self.assertEqual(result[0]["title"], "Review 7")
        self.assertEqual(result[-1]["title"], "Review 3")

    def test_uses_current_rate_not_historical_cost_and_labels_scenario(self):
        result = web._usage_cost_recommendations(
            [{"channel_name": "Heat Pump", "total_kwh": 10, "total_cents": 137.5}],
            0.2258,
        )[0]
        self.assertIn("$2.26", result["body"])
        self.assertIn("$6.77", result["body"])
        self.assertIn("Not guaranteed savings", result["body"])
        self.assertIn("Fixed charges are excluded", result["body"])

    def test_missing_consumption_does_not_generate_savings(self):
        self.assertEqual(web._usage_cost_recommendations([], 0.2258), [])
        self.assertEqual(web._usage_cost_recommendations([
            {"channel_name": "Unknown", "total_kwh": None},
            {"channel_name": "Export", "total_kwh": -1},
        ], 0.2258), [])
