#!/usr/bin/env python3
"""Render reviewed service templates for native Linux or an Incus/LXD guest.

This prepares files only: it never installs units, starts collectors, copies
credentials, alters a database, or exposes a public listener.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = (
    "energy-dashboard", "energy-poller", "kasa-collector",
    "aqara-local-collector", "ecosense-collector", "mitsubishi-collector",
)
SYSTEM_COLLECTORS = {
    "aqara-local-collector": ("Aqara Matter event collector", "aqara_matter_collect.py"),
    "ecosense-collector": ("EcoSense radon collector", "ecosense_collect.py"),
    "mitsubishi-collector": ("Optional Mitsubishi read-only collector", "mitsubishi_collect.py"),
}


def absolute_path(value: str) -> str:
    path = Path(value)
    if not path.is_absolute() or any(c.isspace() for c in value):
        raise ValueError("Deployment paths must be absolute and contain no whitespace")
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", value) or ".." in path.parts:
        raise ValueError("Unsupported deployment path")
    return value


def prepare(output: Path, user: str, code: str, data: str, environment: str,
            *, kasa_interval: int = 60) -> None:
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", user) or user == "root":
        raise ValueError("Choose a non-root service account")
    if type(kasa_interval) is not int or not 10 <= kasa_interval <= 86400:
        raise ValueError("Kasa interval must be between 10 and 86400 seconds")
    values = {
        "__COLLECTOR_USER__": user,
        "__PROJECT_ROOT__": absolute_path(code),
        "__DATA_ROOT__": absolute_path(data),
        "__PRIVATE_ENV_FILE__": absolute_path(environment),
    }
    # Validate everything before creating output; refuse to overwrite prior plans.
    units = {}
    for name in SERVICES:
        if name in SYSTEM_COLLECTORS:
            # Existing optional templates target user managers. Derive their
            # system variants from the same hardened dashboard template.
            description, module = SYSTEM_COLLECTORS[name]
            text = (ROOT / "setup" / "energy-dashboard.service").read_text()
            text = text.replace("Emporia Energy Monitor loopback dashboard", description)
            text = text.replace("__PROJECT_ROOT__/web.py", f"__PROJECT_ROOT__/{module}")
        else:
            text = (ROOT / "setup" / f"{name}.service").read_text()
        if name == "kasa-collector":
            if text.count("--interval 60") != 1:
                raise ValueError("Kasa service template is missing its interval argument")
            text = text.replace("--interval 60", f"--interval {kasa_interval}")
        for key, value in values.items():
            text = text.replace(key, value)
        if "__" in text:
            raise ValueError(f"Unresolved placeholder in {name}")
        units[name] = text
    output.mkdir(mode=0o700)
    for name, text in units.items():
        (output / f"{name}.service").write_text(text)
    manifest = {
        "version": (ROOT / "VERSION").read_text().strip(),
        "service_user": user,
        "code_root": code,
        "data_root": data,
        "environment_file": environment,
        "kasa_interval_seconds": kasa_interval,
        "automatically_enabled_services": [],
        "templates_sha256": {
            name: hashlib.sha256(text.encode()).hexdigest()
            for name, text in units.items()
        },
    }
    (output / "deployment.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--code", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--kasa-interval", type=int, default=60,
                        help="Seconds between Kasa cycles (10-86400; default: 60)")
    args = parser.parse_args()
    try:
        prepare(args.output, args.user, args.code, args.data, args.environment,
                kasa_interval=args.kasa_interval)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Deployment preparation failed: {exc}\n")
