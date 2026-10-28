"""One logging setup for the poller, dashboard and every collector.

Each line is one event: an event name plus fields, either as ``key=value`` pairs
(the default, readable in journald) or as JSON lines (``LOG_FORMAT=json``).

Environment:

- ``LOG_LEVEL``   root level (DEBUG, INFO, WARNING, ERROR), default INFO.
- ``LOG_LEVELS``  per-logger overrides, e.g. ``energy=DEBUG,kasa_monitor=WARNING``.
- ``LOG_FORMAT``  ``kv`` (default), ``json`` or ``text`` (the legacy plain format).

Use :func:`event` for structured events and :func:`startup` once per process.
Field names that look like secrets are redacted; never pass credentials as fields.
"""
import json
import logging
import math
import os
import re
import sys
from datetime import datetime, timezone

_SECRET = re.compile(r'(pass|secret|token|key|cookie|auth|credential|email)', re.IGNORECASE)
_BARE = re.compile(r'^[][A-Za-z0-9_.:/@+-]+$')
_RESERVED = frozenset(vars(logging.LogRecord('', 0, '', 0, '', (), None))) | {'message', 'asctime'}
_configured = False


def _level(value: str | None, default: int = logging.INFO) -> int:
    if not value:
        return default
    level = logging.getLevelName(value.strip().upper())
    return level if isinstance(level, int) else default


def _clean(key: str, value):
    if _SECRET.search(key) and value not in (None, '', False):
        return '[redacted]'
    if isinstance(value, float):
        return round(value, 4) if math.isfinite(value) else str(value)
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple, set)):
        return [_clean(key, item) for item in value]
    if isinstance(value, dict):
        return {str(k): _clean(str(k), v) for k, v in value.items()}
    return str(value)


def _fields(record: logging.LogRecord) -> dict:
    fields = getattr(record, 'fields', None)
    if isinstance(fields, dict):
        return {str(k): _clean(str(k), v) for k, v in fields.items()}
    return {}


def _kv_value(value) -> str:
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (list, dict)):
        return json.dumps(value, separators=(',', ':'), sort_keys=True)
    text = str(value)
    return text if text and _BARE.match(text) else json.dumps(text)


def _stamp(record: logging.LogRecord) -> str:
    return datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec='milliseconds')


class KeyValueFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        parts = [f'ts={_stamp(record)}', f'level={record.levelname}', f'logger={record.name}']
        name = getattr(record, 'event', None)
        if name:
            parts.append(f'event={_kv_value(name)}')
        parts += [f'{key}={_kv_value(value)}' for key, value in _fields(record).items()]
        message = record.getMessage()
        if message and message != name:
            parts.append(f'msg={_kv_value(message)}')
        if record.exc_info:
            parts.append(f'exc={_kv_value(self.formatException(record.exc_info))}')
        return ' '.join(parts)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {'ts': _stamp(record), 'level': record.levelname, 'logger': record.name}
        name = getattr(record, 'event', None)
        if name:
            data['event'] = name
        data.update(_fields(record))
        message = record.getMessage()
        if message and message != name:
            data['msg'] = message
        if record.exc_info:
            data['exc'] = self.formatException(record.exc_info)
        return json.dumps(data, default=str, separators=(',', ':'))


def formatter(kind: str | None = None) -> logging.Formatter:
    kind = (kind or os.environ.get('LOG_FORMAT') or 'kv').strip().lower()
    if kind == 'json':
        return JsonFormatter()
    if kind == 'text':
        return logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    return KeyValueFormatter()


def configure(force: bool = False) -> None:
    """Install one stderr handler on the root logger; repeated calls are no-ops."""
    global _configured
    if _configured and not force:
        return
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, '_energy_log_setup', False):
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler._energy_log_setup = True  # type: ignore[attr-defined]
    handler.setFormatter(formatter())
    root.addHandler(handler)
    root.setLevel(_level(os.environ.get('LOG_LEVEL')))
    for item in (os.environ.get('LOG_LEVELS') or '').split(','):
        name, _, value = item.partition('=')
        if name.strip() and value.strip():
            logging.getLogger(name.strip()).setLevel(_level(value, logging.NOTSET))
    _configured = True


def event(logger: logging.Logger, name: str, level: int = logging.INFO,
          exc_info=None, **fields) -> None:
    """Log one structured event: ``event(log, 'poll_cycle', duration_s=1.2, readings=19)``."""
    if logger.isEnabledFor(level):
        logger.log(level, name, extra={'event': name, 'fields': fields}, exc_info=exc_info)


def startup(logger: logging.Logger, service: str, **config) -> None:
    """One line per process start with its effective, non-secret configuration."""
    event(logger, 'startup', service=service, pid=os.getpid(),
          python=sys.version.split()[0], log_level=logging.getLevelName(logging.getLogger().level),
          log_format=(os.environ.get('LOG_FORMAT') or 'kv'), **config)


# Fields reserved by LogRecord cannot be used as structured field names.
assert not ({'event', 'fields'} & _RESERVED)
