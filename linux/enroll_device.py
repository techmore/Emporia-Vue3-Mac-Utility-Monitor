#!/usr/bin/env python3
"""Register a separate device password at the deployed oauth2-proxy gateway.

Requires an administrator with Incus access and a collector with Python 3.12's
crypt module. The password travels only over SSH stdin; only a bcrypt hash is
stored on the server. No SSH connection is used by the running desktop client.
"""
import argparse
import json
import os
import re
import secrets
import shlex
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser(description="Enroll this desktop once; SSH is used only for administrator setup.")
parser.add_argument('--ssh-target', required=True)
parser.add_argument('--container', default='energy-monitor')
parser.add_argument('--device-name', required=True)
args = parser.parse_args()
if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*', args.ssh_target):
    parser.error('Use user@hostname for the administrative SSH target.')
if not all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', value)
           for value in (args.container, args.device_name)):
    parser.error('Container and device names must contain only letters, numbers, _ and -.')

path = Path.home()/'.config/energy-monitor/device.json'
path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
if path.exists():
    if path.stat().st_mode & 0o077:
        parser.error('Existing device credential must have mode 0600.')
    credential = json.loads(path.read_text())
    if credential.get('origin') != 'https://energy.stoverparc.org' or credential.get('username') != args.device_name:
        parser.error('Existing device credential belongs to a different origin or device name.')
else:
    credential = {'origin': 'https://energy.stoverparc.org',
                  'username': args.device_name, 'password': secrets.token_urlsafe(48)}
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(credential, stream)
remote = '''import crypt,json,os,pwd,sys
from pathlib import Path
v=json.load(sys.stdin)
p=Path('/etc/energy-device.htpasswd')
lines=p.read_text().splitlines() if p.exists() else []
lines=[s for s in lines if not s.startswith(v['username']+':')]
lines.append(v['username']+':'+crypt.crypt(v['password'], crypt.mksalt(crypt.METHOD_BLOWFISH)))
p.write_text('\\n'.join(lines)+'\\n')
u=pwd.getpwnam('oauth2-proxy'); os.chown(p,u.pw_uid,u.pw_gid); p.chmod(0o600)
c=Path('/etc/energy-oauth2-proxy.cfg')
b=c.with_suffix('.cfg.pre-device-20261018')
if not b.exists():
    b.write_bytes(c.read_bytes()); b.chmod(0o600)
s='\\n'.join(l for l in c.read_text().splitlines() if not l.startswith(('htpasswd_file =','display_htpasswd_form =')))
c.write_text(s+'\\nhtpasswd_file = "/etc/energy-device.htpasswd"\\ndisplay_htpasswd_form = false\\n')
'''
# Pass the credential only through the SSH stdin stream, never arguments or logs.
command = 'incus exec ' + args.container + ' -- /usr/bin/python3 -c ' + shlex.quote(remote)
subprocess.run(['ssh', '-o', 'BatchMode=yes', args.ssh_target, command],
               input=json.dumps(credential).encode(), check=True)
subprocess.run(['ssh', '-o', 'BatchMode=yes', args.ssh_target,
                'incus exec ' + args.container + ' -- systemctl restart energy-oauth2-proxy'], check=True)
print('Device credential registered; private local credential saved.')
