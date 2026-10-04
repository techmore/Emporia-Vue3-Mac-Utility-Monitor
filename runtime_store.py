"""Atomic, owner-only runtime JSON persistence shared by web and poller."""
import json
import os
from pathlib import Path
import tempfile


def write_private_json(path: str | Path, data: dict) -> None:
    target = Path(path)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)
