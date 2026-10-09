"""Private verified offline snapshot copies; never opens SQLite or installs data."""

import hashlib
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path


def file_sha256(path: Path) -> str:
    with path.open('rb') as stream:
        return _digest(stream)


def _digest(stream) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def verified_copy(snapshot: Path, destination: Path, expected_sha256: str):
    """Publish only after the caller closes a successful standalone working copy."""
    if not isinstance(expected_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_sha256):
        raise ValueError('Provide the verified snapshot SHA-256')
    snapshot, destination = Path(snapshot).absolute(), Path(destination).absolute()
    if snapshot.is_symlink() or destination.is_symlink() or destination.exists():
        raise ValueError('Source must not be a symlink; destination must not exist')
    if destination.parent.stat().st_mode & 0o077:
        raise ValueError('Destination directory must be private (0700)')
    if any(Path(str(snapshot) + suffix).exists() or Path(str(snapshot) + suffix).is_symlink()
           for suffix in ('-wal', '-shm', '-journal')):
        raise ValueError('Use a standalone snapshot, not a live database')
    fd = os.open(snapshot, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    temporary = None
    try:
        with os.fdopen(fd, 'rb') as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077 or metadata.st_nlink != 1:
                raise ValueError('Snapshot must be a private regular file')
            if _digest(source) != expected_sha256:
                raise ValueError('Snapshot hash does not match its receipt')
            source.seek(0)
            fd, name = tempfile.mkstemp(prefix='.identity-reconciliation-', dir=destination.parent)
            temporary = Path(name)
            with os.fdopen(fd, 'wb') as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            if file_sha256(temporary) != expected_sha256:
                raise RuntimeError('Snapshot changed during copying')
            yield temporary
            source.seek(0)
            current = snapshot.stat(follow_symlinks=False)
            if ((current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino)
                    or _digest(source) != expected_sha256):
                raise RuntimeError('Archived snapshot changed during review')
            if any(Path(str(temporary) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
                raise RuntimeError('Working copy was not finalized as standalone SQLite')
            with temporary.open('rb') as result:
                os.fsync(result.fileno())
            os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
            for suffix in ('-wal', '-shm', '-journal'):
                Path(str(temporary) + suffix).unlink(missing_ok=True)
