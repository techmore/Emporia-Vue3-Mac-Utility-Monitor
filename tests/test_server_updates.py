import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError


spec = importlib.util.spec_from_file_location(
    "server_updates", Path(__file__).resolve().parents[1] / "scripts/check_server_updates.py",
)
updates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updates)


class ServerUpdateTests(unittest.TestCase):
    def check(self, current="2.3.37", main="2.3.39", release="v2.3.26", release_error=None):
        commit = "a" * 40
        responses = {
            "http://localhost/api/version": json.dumps({"version": current}),
            f"{updates.API}/git/ref/heads/main": json.dumps({
                "object": {"sha": commit, "type": "commit"},
            }),
            f"https://raw.githubusercontent.com/{updates.REPOSITORY}/{commit}/VERSION": main,
            f"{updates.API}/releases/latest": json.dumps({"tag_name": release}),
        }

        def fetch(url):
            if url.endswith("/releases/latest") and release_error is not None:
                raise release_error
            return responses[url]

        return updates.check_updates("http://localhost/api/version", fetch)

    def test_merged_source_detected_when_release_is_older(self):
        result = self.check()
        self.assertTrue(result["main_update_available"])
        self.assertFalse(result["release_update_available"])
        self.assertEqual(result["main_commit"], "a" * 40)

    def test_compare_versions_numerically(self):
        result = self.check(current="2.3.9", main="2.3.10", release="v2.3.10")
        self.assertTrue(result["release_update_available"])

    def test_equal_or_older_versions_do_not_trigger_update(self):
        result = self.check(current="2.3.39", main="2.3.39", release="v2.3.26")
        self.assertFalse(result["main_update_available"])
        self.assertFalse(result["release_update_available"])

    def test_no_published_release_does_not_hide_main_update(self):
        result = self.check(release_error=HTTPError("https://github.com", 404, "missing", {}, None))
        self.assertTrue(result["main_update_available"])
        self.assertIsNone(result["latest_release_version"])
        self.assertEqual(result["status"], "checked")

    def test_failed_release_check_is_partial(self):
        result = self.check(release_error=HTTPError("https://github.com", 403, "limited", {}, None))
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["release_update_available"])

    def test_invalid_main_version_fails(self):
        with self.assertRaises(ValueError):
            self.check(main="unreviewed-preview")

    def test_atomic_status_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            updates.write_status(path, {"status": "checked"})
            updates.write_status(path, {"status": "error"})
            self.assertEqual(json.loads(path.read_text())["status"], "error")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertFalse(path.with_name("status.json.tmp").exists())
