import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "deployment_plan", Path(__file__).resolve().parents[1]
    / "scripts" / "prepare_linux_deployment.py",
)
plan = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plan)


class DeploymentPlanTests(unittest.TestCase):
    def test_all_units_render_without_starting_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "plan"
            plan.prepare(output, "energy", "/opt/energy", "/var/lib/energy",
                         "/etc/energy/collector.env")
            self.assertEqual(len(list(output.glob("*.service"))), 6)
            for unit in output.glob("*.service"):
                text = unit.read_text()
                self.assertNotIn("__", text)
                self.assertIn("User=energy", text)
                self.assertIn("UMask=0077", text)
            manifest = json.loads((output / "deployment.json").read_text())
            self.assertEqual(manifest["automatically_enabled_services"], [])
            self.assertEqual(manifest["kasa_interval_seconds"], 60)
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(FileExistsError):
                plan.prepare(output, "energy", "/opt/energy", "/var/lib/energy",
                             "/etc/energy/collector.env")

    def test_unsafe_inputs_never_create_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "plan"
            for user, code in [("root", "/opt/energy"), ("energy\nExecStart=bad", "/opt"),
                               ("energy", "relative"), ("energy", "/opt/a b"),
                               ("energy", "/opt/../etc"), ("energy", "/opt/%u")]:
                with self.subTest(user=user, code=code), self.assertRaises(ValueError):
                    plan.prepare(output, user, code, "/var/lib/energy", "/etc/energy/env")
                self.assertFalse(output.exists())

    def test_deployed_kasa_interval_is_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "plan"
            plan.prepare(output, "energy", "/opt/energy", "/var/lib/energy",
                         "/etc/energy/collector.env", kasa_interval=10)
            self.assertIn("kasa_collect.py --interval 10",
                          (output / "kasa-collector.service").read_text())
            manifest = json.loads((output / "deployment.json").read_text())
            self.assertEqual(manifest["kasa_interval_seconds"], 10)

    def test_invalid_intervals_never_create_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "plan"
            for interval in (0, 9, 86401, True, "10", 10.5):
                with self.subTest(interval=interval), self.assertRaises(ValueError):
                    plan.prepare(output, "energy", "/opt/energy", "/var/lib/energy",
                                 "/etc/energy/collector.env", kasa_interval=interval)
                self.assertFalse(output.exists())
