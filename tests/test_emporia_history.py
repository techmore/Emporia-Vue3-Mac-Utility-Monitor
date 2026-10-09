import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from emporia_history import validate_completed_minutes


def evidence():
    return {
        "schema": "emporia_chart_v1",
        "request": {
            "start": "2026-10-09T06:17:00Z",
            "end": "2026-10-09T06:22:00Z",
            "scale": "1MIN",
            "unit": "KilowattHours",
        },
        "received_at": "2026-10-09T06:30:00Z",
        "response": {"firstUsageInstant": "2026-10-09T06:17:00Z", "usageList": [0.01] * 6},
    }


class EmporiaHistoryTests(unittest.TestCase):
    def test_inclusive_api_end_does_not_add_an_extra_minute(self):
        result = validate_completed_minutes(evidence())
        self.assertEqual(result["eligible_completed_buckets"], 5)
        self.assertEqual(result["excluded_window"], 1)
        self.assertEqual(result["buckets"][-1]["end_utc"], "2026-10-09T06:22:00.000000+00:00")
        self.assertAlmostEqual(sum(r["usage_kwh"] for r in result["buckets"]), 0.05)
        self.assertFalse(result["publication_performed"])
        self.assertFalse(result["continuous_capture_verified"])

    def test_missing_or_naive_server_anchor_never_falls_back_to_request(self):
        for value in (None, "2026-10-09T06:17:00", "garbage"):
            item = evidence()
            item["response"]["firstUsageInstant"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_completed_minutes(item)

    def test_provider_live_instant_is_not_a_chart_anchor(self):
        item = evidence()
        item["response"] = {"instant": "2026-10-09T06:17:00Z", "usageList": [0.01] * 6}
        with self.assertRaises(ValueError):
            validate_completed_minutes(item)

    def test_signed_zero_and_missing_are_distinct(self):
        item = evidence()
        item["response"]["usageList"] = [0, -0.01, None, 0.02, 0.03, 99]
        result = validate_completed_minutes(item)
        self.assertEqual([r["usage_kwh"] for r in result["buckets"]], [0, -0.01, 0.02, 0.03])
        self.assertEqual(result["missing_completed_buckets"], 1)
        self.assertEqual([r["source_index"] for r in result["buckets"]], [0, 1, 3, 4])

    def test_unsettled_buckets_are_not_admitted(self):
        item = evidence()
        item["received_at"] = "2026-10-09T06:25:00Z"
        result = validate_completed_minutes(item)
        self.assertEqual(result["eligible_completed_buckets"], 3)
        self.assertEqual(result["excluded_unsettled"], 2)
        self.assertEqual(result["excluded_window"], 1)

    def test_offsets_preserve_elapsed_buckets_across_fold(self):
        item = evidence()
        item["request"].update(start="2026-11-01T01:58:00-04:00", end="2026-11-01T01:03:00-05:00")
        item["response"]["firstUsageInstant"] = "2026-11-01T05:58:00Z"
        item["received_at"] = "2026-11-01T06:10:00Z"
        result = validate_completed_minutes(item)
        self.assertEqual(result["eligible_completed_buckets"], 5)
        self.assertEqual(result["buckets"][2]["start_utc"], "2026-11-01T06:00:00.000000+00:00")

    def test_partial_requested_edge_is_excluded_not_prorated(self):
        item = evidence()
        item["request"].update(start="2026-10-09T06:17:30Z", end="2026-10-09T06:21:30Z")
        result = validate_completed_minutes(item)
        self.assertEqual(result["eligible_completed_buckets"], 3)
        self.assertEqual(result["excluded_window"], 3)

    def test_returned_anchor_not_requested_start_controls_positions(self):
        item = evidence()
        item["response"]["firstUsageInstant"] = "2026-10-09T06:18:00Z"
        result = validate_completed_minutes(item)
        self.assertEqual(result["eligible_completed_buckets"], 4)
        self.assertEqual(result["buckets"][0]["source_index"], 0)
        self.assertEqual(result["buckets"][0]["start_utc"], "2026-10-09T06:18:00.000000+00:00")

    def test_invalid_values_fail_entire_report_even_outside_window(self):
        for value in (True, "0.01", float("nan"), float("inf"), {}, 10**1000):
            item = evidence()
            item["response"]["usageList"][-1] = value
            with self.subTest(kind=type(value)), self.assertRaises(ValueError):
                validate_completed_minutes(item)

    def test_explicit_request_format_and_units_are_required(self):
        for path, value in (
            ("schema", "other"),
            ("scale", "1H"),
            ("unit", "Watts"),
            ("start", "2026-10-09T06:17:00"),
            ("end", "2026-10-09T06:17:00Z"),
        ):
            item = evidence()
            (item if path == "schema" else item["request"])[path] = value
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_completed_minutes(item)

    def test_size_alignment_and_delay_guards(self):
        for first, values in (
            ("2026-10-09T06:17:01Z", [0.01]),
            ("2026-10-09T06:17:00Z", [0.01] * 10082),
            ("9999-12-31T23:59:00Z", [0.01] * 2),
        ):
            item = evidence()
            item["response"].update(firstUsageInstant=first, usageList=values)
            with self.subTest(first=first), self.assertRaises(ValueError):
                validate_completed_minutes(item)
        for delay in (True, -1, 86401, 1.5):
            with self.subTest(delay=delay), self.assertRaises(ValueError):
                validate_completed_minutes(evidence(), settling_seconds=delay)

    def test_empty_and_all_null_do_not_claim_coverage(self):
        for values in ([], [None] * 6):
            item = evidence()
            item["response"]["usageList"] = values
            result = validate_completed_minutes(item)
            self.assertEqual(result["eligible_completed_buckets"], 0)
            self.assertEqual(result["expected_completed_buckets"], 5)
            self.assertEqual(result["missing_completed_buckets"], 5)
            self.assertEqual(result["unreported_completed_buckets"], 5 if not values else 0)

    def test_unreturned_leading_and_trailing_buckets_remain_missing(self):
        item = evidence()
        item["response"].update(firstUsageInstant="2026-10-09T06:18:00Z", usageList=[0.01, None])
        result = validate_completed_minutes(item)
        self.assertEqual(result["expected_completed_buckets"], 5)
        self.assertEqual(result["eligible_completed_buckets"], 1)
        self.assertEqual(result["reported_null_buckets"], 1)
        self.assertEqual(result["unreported_completed_buckets"], 3)
        self.assertEqual(result["missing_completed_buckets"], 4)

    def test_invalid_window_and_receipt_cannot_authorize_measurements(self):
        for key, value in (
            ("end", "2026-10-17T06:22:00Z"),
            ("received_at", "2026-10-09T06:30:00"),
            ("received_at", "0001-01-01T00:00:00Z"),
        ):
            item = evidence()
            (item if key == "received_at" else item["request"])[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_completed_minutes(item)

    def test_unsupported_shapes_fail_closed(self):
        for item in (
            None,
            [],
            {},
            {"schema": "emporia_chart_v1", "request": []},
            {
                **evidence(),
                "response": {"firstUsageInstant": "2026-10-09T06:17:00Z", "usageList": {}},
            },
        ):
            with self.subTest(kind=type(item)), self.assertRaises(ValueError):
                validate_completed_minutes(item)

    def test_evidence_is_not_mutated(self):
        item = evidence()
        before = copy.deepcopy(item)
        validate_completed_minutes(item)
        self.assertEqual(item, before)

    def test_cli_is_private_read_only_and_reports_exact_input_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = json.dumps(evidence()).encode()
            path = root / "capture.json"
            path.write_bytes(data)
            script = Path(__file__).resolve().parents[1] / "scripts/audit_emporia_history.py"
            env = {**os.environ, "DB_PATH": str(root / "must-not-create.db")}
            result = subprocess.run(
                [sys.executable, str(script), "--input", str(path)],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            report = json.loads(result.stdout)
            self.assertEqual(report["evidence_sha256"], hashlib.sha256(data).hexdigest())
            self.assertNotIn("buckets", report)
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["capture.json"])
            result = subprocess.run(
                [sys.executable, str(script), "--input", str(path), "--include-buckets"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            self.assertEqual(len(json.loads(result.stdout)["buckets"]), 5)

    def test_cli_errors_do_not_echo_private_payload_or_path(self):
        with tempfile.TemporaryDirectory(prefix="private-secret-") as directory:
            path = Path(directory) / "private-token.json"
            path.write_text('{"private-token": NaN}')
            script = Path(__file__).resolve().parents[1] / "scripts/audit_emporia_history.py"
            result = subprocess.run(
                [sys.executable, str(script), "--input", str(path)], capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertNotIn("private-token", result.stderr)
            self.assertNotIn(directory, result.stderr)
