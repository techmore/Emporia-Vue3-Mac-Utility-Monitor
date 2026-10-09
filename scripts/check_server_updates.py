#!/usr/bin/env python3
"""Check deployed version against published releases and immutable main source."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen


REPOSITORY = "techmore/Emporia-Vue3-Mac-Utility-Monitor"
API = f"https://api.github.com/repos/{REPOSITORY}"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


def version_parts(value):
    if not isinstance(value, str) or not re.fullmatch(r"v?\d+\.\d+\.\d+", value):
        raise ValueError("Expected a stable major.minor.patch version")
    return tuple(int(part) for part in value.removeprefix("v").split("."))


def fetch(url):
    request = Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "Emporia-Energy-Monitor-Update-Check",
    })
    with urlopen(request, timeout=15) as response:
        data = response.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("Update response exceeds size limit")
    return data.decode("utf-8")


def check_updates(current_url, fetcher=fetch):
    current = json.loads(fetcher(current_url))["version"]
    current_parts = version_parts(current)
    reference = json.loads(fetcher(f"{API}/git/ref/heads/main"))
    commit = reference["object"]["sha"]
    if reference["object"].get("type") != "commit" or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Invalid main commit reference")
    main_version = fetcher(
        f"https://raw.githubusercontent.com/{REPOSITORY}/{commit}/VERSION"
    ).strip()
    main_parts = version_parts(main_version)
    release = None
    release_error = None
    try:
        release_data = json.loads(fetcher(f"{API}/releases/latest"))
        if release_data.get("draft") or release_data.get("prerelease"):
            raise ValueError("Latest release must be published and stable")
        release = release_data["tag_name"]
        version_parts(release)
    except HTTPError as error:
        if error.code != 404:
            release_error = f"HTTP {error.code}"
    except (OSError, ValueError, KeyError) as error:
        release_error = type(error).__name__
    return {
        "status": "checked" if release_error is None else "partial",
        "deployed_version": current,
        "main_version": main_version,
        "main_commit": commit,
        "main_update_available": main_parts > current_parts,
        "latest_release_version": release,
        "release_update_available": (
            version_parts(release) > current_parts if release is not None else None
        ),
        "release_check_error": release_error,
    }


def write_status(path, result):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump(result, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-url", default="http://127.0.0.1:5051/api/version")
    parser.add_argument("--status-file", default="/var/lib/energy-monitor-updates/status.json")
    args = parser.parse_args()
    checked_at = datetime.now(timezone.utc).isoformat()
    try:
        result = check_updates(args.current_url)
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {"status": "error", "error": type(error).__name__}
    result["checked_at"] = checked_at
    write_status(args.status_file, result)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["status"] == "checked" else 1


if __name__ == "__main__":
    sys.exit(main())
