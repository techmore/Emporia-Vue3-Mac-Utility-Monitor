#!/usr/bin/env python3
"""Opt-in macOS HTTP watchdog for an existing, explicitly configured SSH tunnel."""
import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from collector_tunnel_watchdog import LABEL, tunnel_arguments, write_private

WATCHDOG_LABEL = "com.dolbec.energymonitor.tunnel-watchdog"


def install(target: str, local_port: int, remote_port: int, python: Path,
            directory: Path, home: Path | None = None) -> Path:
    home = home or Path.home()
    arguments = tunnel_arguments(target, local_port, remote_port)
    if not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("Choose an existing absolute Python executable")
    if not directory.is_absolute() or directory.is_symlink():
        raise ValueError("Recovery directory must be absolute and not a symlink")
    agents = home / "Library/LaunchAgents"
    tunnel = agents / f"{LABEL}.plist"
    stat = tunnel.lstat()
    if tunnel.is_symlink() or stat.st_uid != os.getuid() or not tunnel.is_file():
        raise ValueError("Tunnel must be a regular LaunchAgent owned by this user")
    existing = plistlib.loads(tunnel.read_bytes())
    if existing.get("Label") != LABEL or existing.get("ProgramArguments") != arguments:
        raise ValueError("Existing tunnel does not match; no files were changed")
    destination = agents / f"{WATCHDOG_LABEL}.plist"
    if destination.exists() or destination.is_symlink():
        stat = destination.lstat()
        if destination.is_symlink() or stat.st_uid != os.getuid():
            raise ValueError("Existing watchdog agent is not owned by this user")
        if plistlib.loads(destination.read_bytes()).get("Label") != WATCHDOG_LABEL:
            raise ValueError("Refusing to replace an unrelated agent")
    # Only harden this verified agent's file permissions. Its arguments, log
    # paths and running process are preserved; no unrelated agent is touched.
    if directory.exists():
        if not directory.is_dir() or directory.stat().st_uid != os.getuid():
            raise ValueError("Recovery directory must be owned by this user")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    tunnel.chmod(0o600)
    script = directory / "collector_tunnel_watchdog.py"
    source = Path(__file__).with_name("collector_tunnel_watchdog.py")
    fd, temporary = tempfile.mkstemp(prefix=".watchdog.", dir=directory)
    os.close(fd)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, script)
    finally:
        Path(temporary).unlink(missing_ok=True)
    config = directory / "watchdog-config.json"
    write_private(config, {"ssh_target": target, "local_port": local_port,
                           "remote_port": remote_port})
    agent = {
        "Label": WATCHDOG_LABEL,
        "ProgramArguments": [str(python), "-u", str(script), "--config", str(config)],
        "RunAtLoad": True,
        "StartInterval": 30,
        "ThrottleInterval": 20,
        "StandardOutPath": str(directory / "watchdog.log"),
        "StandardErrorPath": str(directory / "watchdog.log"),
        "Umask": 0o077,
    }
    fd, temporary = tempfile.mkstemp(prefix=".tunnel-watchdog.", dir=agents)
    try:
        with os.fdopen(fd, "wb") as handle:
            plistlib.dump(agent, handle)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination


def activate(agent: Path) -> None:
    label = f"gui/{os.getuid()}/{WATCHDOG_LABEL}"
    loaded = subprocess.run(["/bin/launchctl", "print", label],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=5, check=False)
    if loaded.returncode == 0:
        subprocess.run(["/bin/launchctl", "bootout", label], check=True, timeout=10)
    subprocess.run(["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(agent)],
                   check=True, timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-target", required=True)
    parser.add_argument("--local-port", type=int, default=15001)
    parser.add_argument("--remote-port", type=int, default=5051)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--directory", type=Path, default=Path.home()
                        / "Library/Application Support/EnergyMonitor/tunnel-recovery")
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.exit(1, "This installer requires macOS launchd.\n")
    try:
        agent = install(args.ssh_target, args.local_port, args.remote_port,
                        args.python, args.directory)
        if args.activate:
            activate(agent)
        print("Collector recovery installed" + (" and activated" if args.activate else " (not activated)"))
    except (OSError, ValueError, subprocess.SubprocessError):
        parser.exit(1, "Could not install recovery; verify your tunnel and Python paths.\n")
