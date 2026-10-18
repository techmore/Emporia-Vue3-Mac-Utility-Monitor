"""Atomic, owner-only runtime JSON persistence shared by web and poller."""
import fcntl
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path


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


def update_private_json(path: str | Path, change: Callable[[dict], None]) -> dict:
    """Serialize the entire read/modify/replace cycle across threads and processes.

    Keep the lock inode separate from the replaced JSON file and never unlink it.
    Invalid existing state must fail closed instead of being overwritten as empty.
    """
    target = Path(path).absolute()
    descriptor = os.open(target.with_name('.' + target.name + '.lock'),
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            data = json.loads(target.read_text())
        except FileNotFoundError:
            data = {}
        if not isinstance(data, dict):
            raise ValueError('Runtime JSON must contain an object')
        change(data)
        write_private_json(target, data)
        return data


def publish_private_text(path: str | Path, text: str) -> bool:
    """Atomically publish a complete UTF-8 file without replacing an existing one."""
    target = Path(path)
    descriptor, name = tempfile.mkstemp(prefix=f'.{target.name}.', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(name, target)
        except FileExistsError:
            return False
        return True
    finally:
        Path(name).unlink(missing_ok=True)
