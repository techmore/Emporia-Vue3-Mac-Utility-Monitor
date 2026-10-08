import fcntl
import importlib.util
import json
import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def module(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


watchdog = module("collector_tunnel_watchdog")
with patch.dict(sys.modules, collector_tunnel_watchdog=watchdog):
    installer = module("install_collector_recovery")
CONFIG = {"ssh_target": "user@server.example", "local_port": 15001, "remote_port": 5051}


class CollectorRecoveryTests(unittest.TestCase):
    def test_transient_errors_do_not_restart(self):
        with patch.object(watchdog, "local_health", return_value=False), \
                patch.object(watchdog, "remote_health") as remote, \
                patch.object(watchdog, "restart_agent") as restart:
            state = watchdog.tick(CONFIG, {}, 10)
            state = watchdog.tick(CONFIG, state, 40)
            self.assertEqual(state["failures"], 2)
            remote.assert_not_called()
            restart.assert_not_called()

    def test_healthy_remote_recovers_only_after_three_failures(self):
        with patch.object(watchdog, "local_health", return_value=False), \
                patch.object(watchdog, "agent_loaded", return_value=True), \
                patch.object(watchdog, "remote_health", return_value=True), \
                patch.object(watchdog, "restart_agent", return_value=True) as restart:
            state = watchdog.tick(CONFIG, {"failures": 2}, 100)
            self.assertEqual(state["status"], "tunnel_restarting")
            self.assertEqual(state["recoveries"], 1)
            watchdog.tick(CONFIG, state, 130)
            restart.assert_called_once_with()

    def test_remote_outage_does_not_restart_or_hammer(self):
        with patch.object(watchdog, "local_health", return_value=False), \
                patch.object(watchdog, "agent_loaded", return_value=True), \
                patch.object(watchdog, "remote_health", return_value=False) as remote, \
                patch.object(watchdog, "restart_agent") as restart:
            state = watchdog.tick(CONFIG, {"failures": 2}, 100)
            self.assertEqual(state["status"], "collector_or_network_unavailable")
            state = watchdog.tick(CONFIG, state, 130)
            remote.assert_called_once()
            state = watchdog.tick(CONFIG, state, 221)
            self.assertEqual(state["next_probe_at"], 461)
            restart.assert_not_called()

    def test_unloaded_agent_is_not_bootstrapped(self):
        with patch.object(watchdog, "local_health", return_value=False), \
                patch.object(watchdog, "agent_loaded", return_value=False), \
                patch.object(watchdog, "remote_health") as remote, \
                patch.object(watchdog, "restart_agent") as restart:
            state = watchdog.tick(CONFIG, {"failures": 2}, 100)
            self.assertEqual(state["status"], "tunnel_not_loaded")
            remote.assert_not_called()
            restart.assert_not_called()

    def test_healthy_probe_resets_failures_but_preserves_cooldown(self):
        with patch.object(watchdog, "local_health", return_value=True):
            state = watchdog.tick(CONFIG, {"failures": 8, "next_probe_at": 500}, 100)
            self.assertEqual(state["status"], "healthy")
            self.assertEqual(state["failures"], 0)
            self.assertEqual(state["next_probe_at"], 500)

    def test_restart_failure_is_explicit(self):
        with patch.object(watchdog, "local_health", return_value=False), \
                patch.object(watchdog, "agent_loaded", return_value=True), \
                patch.object(watchdog, "remote_health", return_value=True), \
                patch.object(watchdog, "restart_agent", return_value=False):
            self.assertEqual(watchdog.tick(CONFIG, {"failures": 2}, 100)["status"],
                             "tunnel_restart_failed")

    def test_remote_probe_is_independent_and_bounded(self):
        with patch.object(watchdog.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b'{"version":"2.3.31"}'
            self.assertTrue(watchdog.remote_health(CONFIG))
            args = run.call_args.args[0]
            self.assertIn("ControlMaster=no", args)
            self.assertIn("ControlPath=none", args)
            self.assertEqual(run.call_args.kwargs["timeout"], 12)

    def test_invalid_origins_ports_and_responses_rejected(self):
        for target in ("-oProxyCommand=bad", "user@host;bad", "host", "user@-host"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                watchdog.tunnel_arguments(target, 15001, 5051)
        for port in (True, 0, "5051", 65536):
            with self.subTest(port=port), self.assertRaises(ValueError):
                watchdog.tunnel_arguments("user@host", port, 5051)
        for data in (b"[]", b'{"version":true}', b'{"version":"unknown"}', b"x" * 4097):
            with self.assertRaises(ValueError):
                watchdog.collector_version(data)

    def test_only_exact_owned_agent_can_be_installed(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            agents = home / "Library/LaunchAgents"
            agents.mkdir(parents=True)
            tunnel = agents / f"{watchdog.LABEL}.plist"
            data = {"Label": watchdog.LABEL,
                    "ProgramArguments": watchdog.validate(CONFIG), "KeepAlive": True}
            tunnel.write_bytes(plistlib.dumps(data))
            output = home / "recovery"
            installed = installer.install(CONFIG["ssh_target"], 15001, 5051,
                                          Path(sys.executable), output, home)
            self.assertEqual(plistlib.loads(tunnel.read_bytes()), data)
            self.assertEqual(tunnel.stat().st_mode & 0o777, 0o600)
            agent = plistlib.loads(installed.read_bytes())
            self.assertEqual(agent["StartInterval"], 30)
            self.assertNotIn("KeepAlive", agent)
            watchdog.validate_agent(CONFIG, tunnel)
            self.assertEqual(json.loads((output / "watchdog-config.json").read_text()), CONFIG)
            data["ProgramArguments"][-1] = "someone@elsewhere"
            tunnel.write_bytes(plistlib.dumps(data))
            with self.assertRaises(ValueError):
                installer.install(CONFIG["ssh_target"], 15001, 5051,
                                  Path(sys.executable), home / "other", home)
            self.assertFalse((home / "other").exists())

    def test_private_files_reject_symlinks_and_public_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            watchdog.write_private(path, CONFIG)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            watchdog.private_file(path)
            link = Path(directory) / "link"
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                watchdog.private_file(link)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                watchdog.private_file(path)

    def test_kickstart_targets_only_our_label(self):
        with patch.object(watchdog.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertTrue(watchdog.restart_agent())
            self.assertEqual(run.call_args.args[0], ["/bin/launchctl", "kickstart", "-k",
                                                    f"gui/{os.getuid()}/{watchdog.LABEL}"])

    def test_overlapping_runs_do_not_probe_or_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            agents = home / "Library/LaunchAgents"
            agents.mkdir(parents=True)
            tunnel = agents / f"{watchdog.LABEL}.plist"
            tunnel.write_bytes(plistlib.dumps({"Label": watchdog.LABEL,
                                               "ProgramArguments": watchdog.validate(CONFIG)}))
            tunnel.chmod(0o600)
            config = home / "watchdog-config.json"
            watchdog.write_private(config, CONFIG)
            lock = home / "watchdog.lock"
            lock.touch(mode=0o600)
            with lock.open("w") as handle, patch.object(watchdog.Path, "home", return_value=home), \
                    patch.object(watchdog, "local_health") as probe:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(watchdog.run(config), 0)
                probe.assert_not_called()
            self.assertFalse((home / "watchdog-status.json").exists())
