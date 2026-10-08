"""Download collector readings into a separate private cache through a secure origin."""

import argparse
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Collector redirects are refused to protect the sync token")


def fetch_page(origin: str, token: str, state: dict, *, stream: str = 'readings') -> dict:
    if stream not in ('readings', 'radon'):
        raise ValueError('Unsupported history stream')
    url = urllib.parse.urlsplit(origin)
    if url.scheme not in {"http", "https"} or not url.hostname:
        raise ValueError("Collector must be an HTTP(S) origin")
    if url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
        raise ValueError("Collector origin cannot contain credentials, paths, or query strings")
    if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Use HTTPS or a loopback SSH tunnel")
    if len(token) < 32:
        raise ValueError("ENERGY_SYNC_TOKEN must have at least 32 characters")
    query = {"after": state["cursor"], "limit": 500}
    if state.get("source_id"):
        query["source_id"] = state["source_id"]
    if state.get("generation_id"):
        query["generation_id"] = state["generation_id"]
    endpoint = origin.rstrip("/") + '/api/sync/' + stream + '?' + urllib.parse.urlencode(query)
    request = urllib.request.Request(endpoint, headers={"Authorization": "Bearer " + token})
    opener = urllib.request.build_opener(NoRedirects())
    with opener.open(request, timeout=30) as response:
        # A bounded page must never consume unbounded client memory.
        payload = response.read(4 * 1024 * 1024 + 1)
    if len(payload) > 4 * 1024 * 1024:
        raise ValueError("Collector response exceeds the page size limit")
    return json.loads(payload)


def _download_radon_pages(origin: str, token: str, max_pages: int,
                          expected_source: str | None = None) -> dict:
    import radon

    if type(max_pages) is not int or max_pages < 1:
        raise ValueError('max_pages must be positive')
    state = radon.get_cache_status()
    for _ in range(max_pages):
        page = fetch_page(origin, token, state, stream='radon')
        if expected_source and page.get('source_id') != expected_source:
            raise ValueError('Collector identity changed during radon snapshot download')
        state = radon.apply_changes(page)
        if not page['has_more']:
            return state
    raise RuntimeError('Radon sync page limit reached; rerun to resume from the saved cursor')


def sync_radon_once(origin: str, token: str, *, max_pages: int = 1000) -> dict:
    import energy
    import radon

    state = radon.get_cache_status()
    try:
        return _download_radon_pages(origin, token, max_pages)
    except urllib.error.HTTPError as exc:
        if exc.code != 409:
            raise
        reset = json.loads(exc.read(4096))
        if not reset.get('reset_required') or reset.get('source_id') != state.get('source_id'):
            raise ValueError('Collector identity changed; use a fresh cache database') from exc
    original = energy.DB_PATH
    with tempfile.TemporaryDirectory(dir=Path(original).absolute().parent) as directory:
        staging = Path(directory) / 'radon-cache.db'
        descriptor = os.open(staging, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        try:
            energy.DB_PATH = str(staging)
            energy.ensure_table()
            result = _download_radon_pages(origin, token, max_pages, state['source_id'])
            destination = energy._connect(original)
            try:
                destination.execute('ATTACH DATABASE ? AS snapshot', (str(staging),))
                with destination:
                    destination.execute('BEGIN IMMEDIATE')
                    current = destination.execute('SELECT * FROM radon_sync_cache_state').fetchone()
                    if (not current or current['source_id'] != state['source_id']
                            or current['cursor'] != state['cursor']
                            or current['generation_id'] != state['generation_id']):
                        raise ValueError('Radon cache changed during snapshot download')
                    for table in ('radon_cached_readings', 'radon_sync_cache_state'):
                        destination.execute(f'DELETE FROM main.{table}')
                        destination.execute(f'INSERT INTO main.{table} SELECT * FROM snapshot.{table}')
            finally:
                destination.close()
            return result
        finally:
            energy.DB_PATH = original


def _download_pages(origin: str, token: str, max_pages: int, expected_source: str | None = None) -> dict:
    import energy

    state = energy.get_sync_cache_status()
    for _ in range(max_pages):
        page = fetch_page(origin, token, state)
        if expected_source and page.get("source_id") != expected_source:
            raise ValueError("Collector identity changed during snapshot download")
        state = energy.apply_reading_changes(page)
        if not page["has_more"]:
            return state
    raise RuntimeError("Sync page limit reached; rerun to resume from the saved cursor")


def sync_once(origin: str, token: str, *, max_pages: int = 1000) -> dict:
    import energy

    conn = energy._connect()
    try:
        if conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0]:
            raise ValueError("Refusing to synchronize into a database containing local readings")
    finally:
        conn.close()
    state = energy.get_sync_cache_status()
    try:
        return _download_pages(origin, token, max_pages)
    except urllib.error.HTTPError as exc:
        if exc.code != 409:
            raise
        reset = json.loads(exc.read(4096))
        if not reset.get("reset_required") or reset.get("source_id") != state.get("source_id"):
            raise ValueError("Collector identity changed; use a fresh cache database") from exc
    original = energy.DB_PATH
    # Keep the old cache available until the entire replacement snapshot is verified.
    with tempfile.TemporaryDirectory(dir=Path(original).absolute().parent) as directory:
        staging = Path(directory) / "cache.db"
        descriptor = os.open(staging, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        try:
            energy.DB_PATH = str(staging)
            energy.ensure_table()
            result = _download_pages(origin, token, max_pages, state["source_id"])
            destination = energy._connect(original)
            try:
                destination.execute("ATTACH DATABASE ? AS snapshot", (str(staging),))
                # A reset owns only energy cache tables, not other sensor history.
                with destination:
                    for table in ("sync_cached_readings", "sync_cache_state", "sync_cache_generation"):
                        destination.execute(f"DELETE FROM main.{table}")
                        destination.execute(f"INSERT INTO main.{table} SELECT * FROM snapshot.{table}")
            finally:
                destination.close()
            return result
        finally:
            energy.DB_PATH = original


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collector", required=True)
    parser.add_argument("--cache", type=Path, required=True,
                        help="Separate cache database, not the local collector database")
    args = parser.parse_args()
    cache = args.cache.expanduser().absolute()
    if cache.is_symlink():
        parser.error("Cache database cannot be a symbolic link")
    if not cache.exists():
        descriptor = os.open(cache, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
    os.chmod(cache, 0o600)
    os.environ["DB_PATH"] = str(cache)
    try:
        state = sync_once(args.collector, os.environ.get("ENERGY_SYNC_TOKEN", ""))
        state['radon'] = sync_radon_once(args.collector, os.environ.get('ENERGY_SYNC_TOKEN', ''))
    except (ValueError, RuntimeError, urllib.error.URLError) as exc:
        parser.exit(1, f"Sync failed; saved cursor retained: {exc}\n")
    print(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
