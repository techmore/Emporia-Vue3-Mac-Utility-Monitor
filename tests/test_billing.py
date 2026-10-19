import json
import unittest
from unittest.mock import mock_open, patch

import web


class BillingSettingsTests(unittest.TestCase):
    def test_fixed_charge_defaults_to_zero_without_settings(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            self.assertEqual(web._read_monthly_fixed_charge(), 0)

    def test_fixed_charge_is_read_separately_and_validated(self):
        for value in (0, 11.99):
            with patch("builtins.open", mock_open(read_data=json.dumps(
                {"monthly_fixed_charge": value}
            ))):
                self.assertEqual(web._read_monthly_fixed_charge(), value)
        for value in (-1, "nan", "inf"):
            with patch("builtins.open", mock_open(read_data=json.dumps(
                {"monthly_fixed_charge": value}
            ))):
                with self.assertRaises(ValueError):
                    web._read_monthly_fixed_charge()

    def test_save_preserves_usage_rate_and_unrelated_settings(self):
        with patch("builtins.open", mock_open(read_data=json.dumps(
            {"rate_cents": 22.58, "aqara": {"account": "retained"}}
        ))), patch.object(web.energy, "_write_json_file") as write, \
                patch.object(web, "_refresh_runtime_config"):
            response = web.app.test_client().post(
                "/api/settings/config", json={"monthly_fixed_charge": 11.99},
            )
        self.assertEqual(response.status_code, 200)
        stored = write.call_args.args[1]
        self.assertEqual(stored["rate_cents"], 22.58)
        self.assertEqual(stored["monthly_fixed_charge"], 11.99)
        self.assertEqual(stored["aqara"]["account"], "retained")

    def test_invalid_charge_is_not_saved(self):
        with patch("builtins.open", mock_open(read_data="{}")), \
                patch.object(web.energy, "_write_json_file") as write:
            response = web.app.test_client().post(
                "/api/settings/config", json={"monthly_fixed_charge": -1},
            )
        self.assertEqual(response.status_code, 400)
        write.assert_not_called()
