#!/usr/bin/env python3
"""Install a persistent SSH forward for an already configured Linux client."""
import argparse
from pathlib import Path
import re
import subprocess


def render_unit(target, local_port, remote_port):
    # Restrict unit-file syntax as well as SSH option injection.
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*', target):
        raise ValueError('Use an SSH destination in user@hostname form.')
    if any(type(port) is not int or not 1024 <= port <= 65535
           for port in (local_port, remote_port)):
        raise ValueError('Ports must be integers from 1024 to 65535.')
    return f'''[Unit]
Description=Energy Monitor private collector SSH tunnel
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStart=/usr/bin/ssh -NT -o BatchMode=yes -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -L 127.0.0.1:{local_port}:127.0.0.1:{remote_port} {target}
Restart=always
RestartSec=10
UMask=0077

[Install]
WantedBy=default.target
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh-target', required=True)
    parser.add_argument('--local-port', type=int, default=15001)
    parser.add_argument('--remote-port', type=int, default=15033)
    args = parser.parse_args()
    try:
        unit = render_unit(args.ssh_target, args.local_port, args.remote_port)
    except ValueError as error:
        parser.error(str(error))
    path = Path.home() / '.config/systemd/user/energy-collector-tunnel.service'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit)
    path.chmod(0o600)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', 'energy-collector-tunnel.service'], check=True)
    subprocess.run(['systemctl', '--user', 'restart', 'energy-collector-tunnel.service'], check=True)
    print(f'Tunnel installed. Verify http://127.0.0.1:{args.local_port}/api/menu-summary '
          'and select that origin in Collector Connection.')


if __name__ == '__main__':
    main()
