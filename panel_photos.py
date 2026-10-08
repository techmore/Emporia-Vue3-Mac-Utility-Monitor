"""Private, bounded panel reference images; originals and metadata are not retained."""
import fcntl
import io
import os
import re
import tempfile
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

import energy

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 24_000_000
MAX_PHOTOS = 6
PHOTO_ID = re.compile(r'[0-9a-f]{32}\.jpg')


def photo_directory() -> Path:
    root = Path(energy.DB_PATH).absolute().parent / 'panel-photos'
    if root.is_symlink():
        raise ValueError('Photo directory cannot be a symbolic link')
    root.mkdir(mode=0o700, exist_ok=True)
    root.chmod(0o700)
    return root


def list_photos() -> list[str]:
    return sorted(p.name for p in photo_directory().iterdir()
                  if PHOTO_ID.fullmatch(p.name) and p.is_file() and not p.is_symlink())


def photo_path(name: str) -> Path:
    if not PHOTO_ID.fullmatch(name):
        raise ValueError('Invalid photo identifier')
    path = photo_directory() / name
    if path.is_symlink():
        raise ValueError('Photo cannot be a symbolic link')
    return path


def save_photo(stream) -> str:
    payload = stream.read(MAX_BYTES + 1)
    if not payload or len(payload) > MAX_BYTES:
        raise ValueError('Photo must be between 1 byte and 10 MiB')
    try:
        with Image.open(io.BytesIO(payload), formats=('JPEG', 'PNG')) as source:
            if source.width * source.height > MAX_PIXELS:
                raise ValueError('Photo exceeds 24 megapixels')
            source.load()
            image = ImageOps.exif_transpose(source).convert('RGB')
            image.thumbnail((2400, 2400))
            # New pixel-only image strips EXIF, GPS, comments and embedded profiles.
            clean = Image.new('RGB', image.size)
            clean.paste(image)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('Upload must be a valid JPEG or PNG image') from exc
    root = photo_directory()
    lock_fd = os.open(root / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if len(list_photos()) >= MAX_PHOTOS:
            raise ValueError('Keep at most six panel photos; remove one before uploading')
        name = uuid.uuid4().hex + '.jpg'
        fd, temporary = tempfile.mkstemp(prefix='.upload-', dir=root)
        try:
            with os.fdopen(fd, 'wb') as output:
                clean.save(output, format='JPEG', quality=88)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, root / name)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return name
