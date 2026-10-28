import io
import json
import logging
import os
import unittest
from unittest.mock import patch

import log_setup


class LogSetupTests(unittest.TestCase):
    def record(self, **fields):
        logger = logging.getLogger('test.log_setup')
        record = logger.makeRecord(logger.name, logging.INFO, __file__, 1, 'poll_cycle', (), None,
                                   extra={'event': 'poll_cycle', 'fields': fields})
        return record

    def test_key_value_line_quotes_spaces_and_redacts_secrets(self):
        line = log_setup.KeyValueFormatter().format(self.record(
            duration_s=1.23456, readings=19, detail='two words', password='hunter2',
            emporia_email='a@b.c', missing=None))
        self.assertIn('event=poll_cycle', line)
        self.assertIn('duration_s=1.2346', line)
        self.assertIn('readings=19', line)
        self.assertIn('detail="two words"', line)
        self.assertIn('password=[redacted]', line)
        self.assertIn('emporia_email=[redacted]', line)
        self.assertNotIn('hunter2', line)
        self.assertIn('missing=null', line)
        self.assertIn('level=INFO', line)

    def test_json_line_is_parseable(self):
        data = json.loads(log_setup.JsonFormatter().format(self.record(readings=3, token='abc')))
        self.assertEqual((data['event'], data['readings'], data['token']), ('poll_cycle', 3, '[redacted]'))
        self.assertEqual(data['logger'], 'test.log_setup')

    def test_plain_messages_still_format(self):
        logger = logging.getLogger('test.plain')
        record = logger.makeRecord(logger.name, logging.WARNING, __file__, 1, 'Rate %s', (5,), None)
        self.assertIn('msg="Rate 5"', log_setup.KeyValueFormatter().format(record))

    def test_configure_reads_level_overrides_and_format(self):
        stream = io.StringIO()
        root = logging.getLogger()
        old_level, old_handlers = root.level, list(root.handlers)
        self.addCleanup(lambda: (root.setLevel(old_level), setattr(root, 'handlers', old_handlers),
                                 logging.getLogger('test.override').setLevel(logging.NOTSET)))
        env = {'LOG_LEVEL': 'warning', 'LOG_LEVELS': 'test.override=DEBUG', 'LOG_FORMAT': 'json'}
        with patch.dict(os.environ, env), patch.object(log_setup.sys, 'stderr', stream):
            log_setup.configure(force=True)
            self.assertEqual(root.level, logging.WARNING)
            log_setup.event(logging.getLogger('test.quiet'), 'skipped_event')
            log_setup.event(logging.getLogger('test.override'), 'debug_event', logging.DEBUG, n=1)
            log_setup.startup(logging.getLogger('test.override'), 'unit-test', db_path='x.db')
        lines = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual([line['event'] for line in lines], ['debug_event', 'startup'])
        self.assertEqual(lines[1]['service'], 'unit-test')
        self.assertEqual(lines[1]['log_level'], 'WARNING')
        self.assertEqual(sum(getattr(h, '_energy_log_setup', False) for h in root.handlers), 1)


if __name__ == '__main__':
    unittest.main()
