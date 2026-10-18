import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import energy
import runtime_store


class ReportPublicationTests(unittest.TestCase):
    def test_partial_write_never_publishes_and_retry_creates_complete_private_report(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = [{'month': '2026-08', 'total_kwh': 1, 'total_cents': 100, 'days_recorded': 1,
                        'circuits': [{'channel_name': 'Pump', 'kwh': 1, 'cents': 100}]}]
            real_fdopen = os.fdopen
            class Interrupted:
                def __init__(self, handle): self.handle = handle
                def __enter__(self): return self
                def __exit__(self, *args): self.handle.close()
                def write(self, text):
                    self.handle.write(text[:20])
                    self.handle.flush()
                    raise OSError('interrupted write')
            def partial(*args, **kwargs):
                return Interrupted(real_fdopen(*args, **kwargs))
            with patch.object(energy, 'get_monthly_costs', return_value=fixture):
                with patch.object(runtime_store.os, 'fdopen', partial):
                    with self.assertRaises(OSError):
                        energy.write_monthly_reports(directory)
                self.assertEqual(list(Path(directory).iterdir()), [])
                report, = energy.write_monthly_reports(directory)
                text = Path(report).read_text()
                self.assertIn('| Circuit | kWh | Cost |', text)
                self.assertTrue(text.endswith('gaps are not estimated.\n'))
                self.assertEqual(Path(report).stat().st_mode & 0o777, 0o600)
                self.assertEqual(energy.write_monthly_reports(directory), [])

    def test_fsync_and_publication_failures_leave_no_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.md'
            for operation in ('fsync', 'link'):
                with patch.object(runtime_store.os, operation, side_effect=OSError('disk failure')):
                    with self.assertRaises(OSError):
                        runtime_store.publish_private_text(path, 'complete report')
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_concurrent_publishers_never_replace_an_existing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.md'
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda text: runtime_store.publish_private_text(path, text),
                                        ['first complete report', 'second complete report']))
            self.assertEqual(sorted(results), [False, True])
            accepted = path.read_text()
            self.assertFalse(runtime_store.publish_private_text(path, 'replacement'))
            self.assertEqual(path.read_text(), accepted)
            self.assertEqual(list(Path(directory).iterdir()), [path])
