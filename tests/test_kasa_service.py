import unittest
from pathlib import Path


class KasaServiceTemplateTests(unittest.TestCase):
    def test_core_processes_share_private_data_and_unbuffered_startup(self):
        root = Path(__file__).resolve().parents[1]
        for name, module in (('energy-poller', 'energy.py'),
                             ('energy-dashboard', 'web.py')):
            with self.subTest(service=name):
                unit = (root / f'setup/{name}.service').read_text()
                self.assertIn(f'python3 -u __PROJECT_ROOT__/{module}', unit)
                self.assertIn('WorkingDirectory=__DATA_ROOT__', unit)
                self.assertIn('ReadWritePaths=__DATA_ROOT__', unit)
                self.assertIn('User=__COLLECTOR_USER__', unit)
                self.assertIn('UMask=0077', unit)
                self.assertIn('EnvironmentFile=__PRIVATE_ENV_FILE__', unit)
                self.assertNotIn('User=root', unit)
        self.assertIn('FLASK_HOST = "127.0.0.1"', (root / 'web.py').read_text())

    def test_service_is_opt_in_unprivileged_and_limits_writes(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / 'setup/kasa-collector.service').read_text()
        required = {
            'User=__COLLECTOR_USER__',
            'WorkingDirectory=__DATA_ROOT__',
            'EnvironmentFile=__PRIVATE_ENV_FILE__',
            'ExecStart=__PROJECT_ROOT__/venv/bin/python3 -u '
            '__PROJECT_ROOT__/kasa_collect.py --interval 60',
            'Restart=on-failure', 'UMask=0077', 'NoNewPrivileges=true',
            'ProtectSystem=strict', 'ProtectHome=read-only',
            'ReadWritePaths=__DATA_ROOT__',
        }
        self.assertTrue(required.issubset(set(template.splitlines())))
        self.assertNotIn('User=root', template)
        self.assertNotIn('KASA_PASSWORD=', template)
        self.assertNotIn('KASA_USERNAME=', template)
        self.assertNotIn('ExecStartPre=', template)
        release = (root / 'release.sh').read_text()
        self.assertIn('for name in setup tests scripts docs templates static', release)

    def test_rendering_requires_explicit_host_paths(self):
        root = Path(__file__).resolve().parents[1]
        rendered = (root / 'setup/kasa-collector.service').read_text()
        for placeholder, value in {
            '__COLLECTOR_USER__': 'energy-monitor',
            '__PROJECT_ROOT__': '/srv/energy-monitor',
            '__DATA_ROOT__': '/var/lib/energy-monitor',
            '__PRIVATE_ENV_FILE__': '/etc/energy-monitor/kasa.env',
        }.items():
            self.assertIn(placeholder, rendered)
            rendered = rendered.replace(placeholder, value)
        self.assertNotIn('__', rendered)
        self.assertIn('User=energy-monitor', rendered)
        self.assertIn('ReadWritePaths=/var/lib/energy-monitor', rendered)
