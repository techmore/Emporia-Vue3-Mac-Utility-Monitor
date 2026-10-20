"""SSH service safety and recovery policy without touching a real service."""
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('tunnel', Path(__file__).parents[1] / 'linux/install_tunnel.py')
tunnel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tunnel)


class TunnelTests(unittest.TestCase):
    def test_loopback_forward_and_recovery(self):
        unit = tunnel.render_unit('owner@collector.example', 5052, 15033)
        for setting in ('-L 127.0.0.1:5052:127.0.0.1:15033', 'Restart=always',
                        'StartLimitIntervalSec=0', 'WantedBy=default.target',
                        'ExitOnForwardFailure=yes', 'BatchMode=yes',
                        'StrictHostKeyChecking=yes', 'ServerAliveCountMax=3'):
            self.assertIn(setting, unit)

    def test_invalid_unit_and_ssh_input_rejected(self):
        for target in ('-oProxyCommand=x', 'a@b\nExecStart=bad', 'a@b%h', 'a@b c', 'a@b;bad'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                tunnel.render_unit(target, 5052, 15033)
        for port in (0, 22, 65536, '5052', True):
            with self.subTest(port=port), self.assertRaises(ValueError):
                tunnel.render_unit('a@b', port, 15033)
