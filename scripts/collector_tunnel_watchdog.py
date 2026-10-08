#!/usr/bin/env python3
"""Check the actual collector HTTP path and recover only our loaded SSH agent."""
import argparse
import fcntl
import json
import os
import plistlib
import re
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

LABEL = "com.dolbec.energymonitor.tunnel"
OPTIONS = ("BatchMode=yes", "StrictHostKeyChecking=yes", "ConnectTimeout=10",
           "ExitOnForwardFailure=yes", "ServerAliveInterval=30", "ServerAliveCountMax=3")


def tunnel_arguments(target: str, local_port: int, remote_port: int) -> list[str]:
    if not isinstance(target, str) or not re.fullmatch(
            r"[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*", target):
        raise ValueError("Use user@hostname or user@IPv4 for the SSH target")
    if any(type(port) is not int or not 1024 <= port <= 65535
           for port in (local_port, remote_port)):
        raise ValueError("Tunnel ports must be integers between 1024 and 65535")
    args = ["/usr/bin/ssh", "-N", "-T"]
    for option in OPTIONS:
        args += ["-o", option]
    return args + ["-L", f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}", target]


def private_file(path: Path) -> None:
    stat = path.lstat()
    if path.is_symlink() or not path.is_file() or stat.st_uid != os.getuid():
        raise ValueError("Recovery files must be regular files owned by this user")
    if stat.st_mode & 0o077:
        raise ValueError("Recovery files must be private (mode 0600)")


def validate(config: dict) -> list[str]:
    if set(config) != {"ssh_target", "local_port", "remote_port"}:
        raise ValueError("Unexpected recovery configuration fields")
    return tunnel_arguments(config["ssh_target"], config["local_port"], config["remote_port"])


def validate_agent(config: dict, path: Path) -> None:
    private_file(path)
    agent = plistlib.loads(path.read_bytes())
    if agent.get("Label") != LABEL or agent.get("ProgramArguments") != validate(config):
        raise ValueError("Tunnel LaunchAgent does not match the configured SSH forward")


def write_private(path: Path, value: dict) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(value, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def collector_version(data: bytes) -> str:
    if len(data) > 4096:
        raise ValueError("Oversized health response")
    value = json.loads(data)
    if not isinstance(value, dict) or not isinstance(value.get("version"), str):
        raise ValueError("Not a collector version response")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", value["version"]):
        raise ValueError("Invalid collector version")
    return value["version"]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_health(config: dict) -> bool:
    try:
        url = f"http://127.0.0.1:{config['local_port']}/api/version"
        request = Request(url, headers={"Cache-Control": "no-cache"})
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=5) as response:
            if response.status != 200:
                return False
            collector_version(response.read(4097))
        return True
    except (OSError, ValueError):
        return False


def remote_health(config: dict) -> bool:
    # Independent SSH connection: a stale multiplexed control socket must not
    # make this diagnostic use the same broken transport as the existing tunnel.
    command = ["/usr/bin/ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", "ConnectTimeout=5", "-o", "ConnectionAttempts=1",
               "-o", "ControlMaster=no", "-o", "ControlPath=none",
               config["ssh_target"],
               "curl --fail --silent --max-time 5 "
               f"http://127.0.0.1:{config['remote_port']}/api/version"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=12, check=False)
        if result.returncode:
            return False
        collector_version(result.stdout)
        return True
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def agent_loaded() -> bool:
    result = subprocess.run(["/bin/launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=5, check=False)
    return result.returncode == 0


def restart_agent() -> bool:
    result = subprocess.run(["/bin/launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LABEL}"],
                            capture_output=True, timeout=8, check=False)
    return result.returncode == 0


def tick(config: dict, state: dict, now: float) -> dict:
    state = dict(state)
    state["checked_at"] = now
    if local_health(config):
        state.update(status="healthy", failures=0, probe_backoff=0, last_healthy_at=now)
        return state
    state["failures"] = min(int(state.get("failures", 0)) + 1, 100)
    if state["failures"] < 3:
        state["status"] = "waiting_for_confirmation"
        return state
    if now < state.get("next_probe_at", 0):
        return state
    backoff = min(int(state.get("probe_backoff", 0)), 4)
    state.update(next_probe_at=now + 120 * 2 ** backoff, probe_backoff=backoff + 1)
    if not agent_loaded():
        # Respect an intentionally unloaded tunnel. Never bootstrap it behind
        # the user's back, and never start a local Flask server or collector.
        state["status"] = "tunnel_not_loaded"
    elif not remote_health(config):
        state["status"] = "collector_or_network_unavailable"
    elif not restart_agent():
        state["status"] = "tunnel_restart_failed"
    else:
        state["recoveries"] = int(state.get("recoveries", 0)) + 1
        state["last_restart_at"] = now
        state["status"] = "tunnel_restarting"
    return state


def run(config_path: Path) -> int:
    private_file(config_path)
    config = json.loads(config_path.read_text())
    validate(config)
    validate_agent(config, Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist")
    directory = config_path.parent
    lock_path = directory / "watchdog.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        private_file(lock_path)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        status_path = directory / "watchdog-status.json"
        state = {}
        if status_path.exists():
            private_file(status_path)
            state = json.loads(status_path.read_text())
            if not isinstance(state, dict):
                raise ValueError("Invalid watchdog state")
        result = tick(config, state, time.time())
        write_private(status_path, result)
        if result.get("status") != state.get("status"):
            print(f"Collector tunnel: {result['status']}", flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        raise SystemExit(run(args.config))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        # Only the exception class is logged; no SSH diagnostics or settings.
        print(f"Collector recovery could not run ({type(exc).__name__})", flush=True)
        raise SystemExit(1) from None
