"""Bounded read-only diagnostics for one explicitly selected local Kasa device."""
import argparse
import asyncio
import getpass
import ipaddress
import json
import logging
import os
import time
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
    from kasa import Discover, Module

    async def read() -> dict:
        device = await Discover.discover_single(
            host, username=username, password=password, discovery_timeout=3, timeout=3,
        )
        if device is None:
            raise ConnectionError('Selected device did not respond')
        try:
            await device.update()
            state = device.is_on
            light = device.modules.get(Module.Light)
            return {'host': host, 'model': device.model, 'alias': device.alias,
                    'device_id': device.device_id,
                    'is_on': state if type(state) is bool else None,
                    'brightness': light.brightness if light is not None else None,
                    'queried_at': datetime.now(timezone.utc).isoformat(),
                    'read_only': True}
        finally:
            try:
                await asyncio.wait_for(device.disconnect(), timeout=2)
            except Exception as exc:
                logging.warning('Kasa disconnect failed: %s', type(exc).__name__)

    started = time.monotonic()
    result = await asyncio.wait_for(read(), timeout=10)
    result.update(duration_ms=round((time.monotonic()-started)*1000, 1), source="poll")
    return result


async def control(host: str, expected_id: str, *, is_on: bool | None = None,
                  brightness: int | None = None) -> dict:
    """Explicit command to a pinned device, with fresh identity and outcome checks."""
    host = validate_host(host)
    if not expected_id or (is_on is None) == (brightness is None):
        raise ValueError('Select one explicit action on a verified device')
    if is_on is not None and type(is_on) is not bool:
        raise ValueError('State must be true or false')
    if brightness is not None and (type(brightness) is not int or not 1 <= brightness <= 100):
        raise ValueError('Brightness must be an integer from 1 to 100; use OFF separately')
    from kasa import Discover, Module

    async def run():
        device = await Discover.discover_single(host, discovery_timeout=3, timeout=3,
                                               username=os.environ.get('KASA_USERNAME'),
                                               password=os.environ.get('KASA_PASSWORD'))
        if device is None:
            raise ConnectionError('No device response')
        try:
            await device.update()
            if device.device_id != expected_id:
                raise ValueError('Device identity changed; no command sent')
            if device.model not in ('HS220', 'HS103'):
                raise ValueError('Controls are not enabled for this model')
            light = device.modules.get(Module.Light)
            if brightness is not None:
                if device.model != 'HS220' or light is None:
                    raise ValueError('Brightness is unavailable; no command sent')
                await light.set_brightness(brightness)
            elif is_on:
                await device.turn_on()
            else:
                await device.turn_off()
            await device.update()
            actual_brightness = light.brightness if light is not None else None
            if (is_on is not None and device.is_on != is_on) or (
                brightness is not None and (actual_brightness != brightness or not device.is_on)
            ):
                raise RuntimeError('Command outcome not verified')
            return {'host': host, 'device_id': device.device_id, 'model': device.model,
                    'alias': device.alias, 'is_on': device.is_on,
                    'brightness': actual_brightness, 'verified': True,
                    'queried_at': datetime.now(timezone.utc).isoformat()}
        finally:
            try:
                await asyncio.wait_for(device.disconnect(), timeout=2)
            except Exception as exc:
                logging.warning('Kasa disconnect failed: %s', type(exc).__name__)

    started = time.monotonic()
    result = await asyncio.wait_for(run(), timeout=15)
    result.update(duration_ms=round((time.monotonic()-started)*1000, 1), source="command")
    logging.warning("Kasa command verified device=%s duration_ms=%s is_on=%s brightness=%s", expected_id, result["duration_ms"], result["is_on"], result["brightness"])
    return result


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
