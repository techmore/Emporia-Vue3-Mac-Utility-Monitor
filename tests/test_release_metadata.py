"""One version source: VERSION, the changelog, the AppStream releases and the indicator's meson project agree."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('check_release', Path(__file__).parents[1] / 'scripts/check_release.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

CHANGELOG = '# Changelog\n\n## Unreleased\n\n- Pending.\n\n## 2.3.65 - 2026-10-18\n\n- Shipped.\n\n## 2.3.64 - 2026-10-18\n'
METAINFO = '<component>\n  <id>org.techmore.EnergyMonitor</id>\n  <releases><release version="2.3.60" date="2026-10-01"/></releases>\n</component>\n'


class ReleaseMetadataTests(unittest.TestCase):
    def test_newest_released_entry_skips_unreleased(self):
        self.assertEqual(check.changelog_release(CHANGELOG), ('2.3.65', '2026-10-18'))
        with self.assertRaises(AssertionError):
            check.changelog_release('# Changelog\n\n## Unreleased\n')

    def test_metainfo_releases_are_generated_and_drift_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / check.METAINFO
            path.parent.mkdir(parents=True)
            path.write_text(METAINFO)
            with self.assertRaises(AssertionError):
                check.check_metainfo(root, '2.3.65', CHANGELOG)
            self.assertIn('2.3.60', path.read_text())  # a failed check never edits the file
            check.check_metainfo(root, '2.3.65', CHANGELOG, write=True)
            self.assertIn('<releases><release version="2.3.65" date="2026-10-18"/></releases>', path.read_text())
            self.assertNotIn('2.3.60', path.read_text())
            self.assertEqual(check.check_metainfo(root, '2.3.65', CHANGELOG), '2026-10-18')
            with self.assertRaises(AssertionError):
                check.check_metainfo(root, '2.3.66', CHANGELOG)  # VERSION ahead of the changelog

    def test_meson_project_takes_its_version_from_the_version_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / check.MESON
            path.parent.mkdir(parents=True)
            path.write_text("project('x', 'vala', 'c', version: '0.1.0')\n")
            with self.assertRaises(AssertionError):
                check.check_meson(root)
            path.write_text("project('x', 'vala', 'c', version: run_command('cat', '../../VERSION', check: true).stdout().strip())\n")
            check.check_meson(root)

    def test_repository_is_in_step(self):
        root = Path(__file__).parents[1]
        version = (root / 'VERSION').read_text().strip()
        self.assertEqual(check.changelog_release((root / 'CHANGELOG.md').read_text())[0], version)
        check.check_metainfo(root, version, (root / 'CHANGELOG.md').read_text())
        check.check_meson(root)
