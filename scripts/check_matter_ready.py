#!/usr/bin/env python3
"""Fail service startup if Matter never opens its loopback API."""
import argparse
import socket
import time


def wait_ready(port=5580, timeout=30):
    deadline = time.monotonic() + timeout
    while True:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                return True
        except OSError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(1, max(0, deadline - time.monotonic())))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=5580)
    parser.add_argument('--timeout', type=float, default=30)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 0 <= args.timeout <= 120:
        parser.error('Invalid port or timeout')
    raise SystemExit(0 if wait_ready(args.port, args.timeout) else 1)
