"""Bounded read-only diagnostics for one explicitly selected local Kasa device."""
import argparse
import asyncio
import getpass
import ipaddress
import json
import logging
import os
from datetime import datetime, timezone


def validate_host(host: str) -> str:
    address = ipaddress.IPv4Address(host)
    allowed = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8')
    if not any(address in ipaddress.IPv4Network(network) for network in allowed):
        raise ValueError('Select a private or loopback IPv4 address')
    return str(address)


async def probe(host: str, username: str | None = None,
                password: str | None = None) -> dict:
    """Return a freshly queried state, not a successful command or energy estimate."""
    host = validate_host(host)
    for value in (username, password):
        if value is not None and (not isinstance(value, str) or len(value) > 1000):
            raise ValueError('Credentials must be strings of at most 1000 characters')
    if bool(username) != bool(password):
        raise ValueError('Both username and password are required for authentication')
    # Optional until the Settings integration and locked dependencies are released.
    from kasa import Discover

    async def read() -> dict:
        device = await Discover.discover_single(
            host, username=username, password=password, discovery_timeout=3, timeout=3,
        )
        if device is None:
            raise ConnectionError('Selected device did not respond')
        try:
            await device.update()
            state = device.is_on
            return {'host': host, 'model': device.model, 'alias': device.alias,
                    'is_on': state if type(state) is bool else None,
                    'queried_at': datetime.now(timezone.utc).isoformat(),
                    'read_only': True}
        finally:
            try:
                await asyncio.wait_for(device.disconnect(), timeout=2)
            except Exception as exc:
                logging.warning('Kasa disconnect failed: %s', type(exc).__name__)

    return await asyncio.wait_for(read(), timeout=10)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True, help='One private IPv4 device address')
    parser.add_argument('--login', action='store_true', help='Prompt without storing credentials')
    args = parser.parse_args()
    try:
        validate_host(args.host)
        username = os.environ.get('KASA_USERNAME') or None
        password = os.environ.get('KASA_PASSWORD') or None
        if args.login:
            username = input('TP-Link account: ').strip()
            password = getpass.getpass('TP-Link password: ')
        result = asyncio.run(probe(args.host, username, password))
    except Exception as exc:
        # Vendor errors can contain addresses or credentials; expose only the class.
        parser.exit(1, f'Kasa read-only probe failed: {type(exc).__name__}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
