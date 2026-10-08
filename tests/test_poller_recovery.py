import unittest
from unittest.mock import patch

import energy


class StopLoop(BaseException):
    pass


class PollerRecoveryTests(unittest.TestCase):
    def test_startup_failures_recover_without_token_deletion(self):
        for failure in ('login', 'discovery', 'empty'):
            with self.subTest(failure=failure):
                client = object()
                logins = [TimeoutError('network'), client] if failure == 'login' else [client, client]
                discoveries = [( ['device'], {})] if failure == 'login' else [
                    TimeoutError('discovery') if failure == 'discovery' else ([], {}),
                    (['device'], {}),
                ]
                with patch.object(energy, 'login_vue', side_effect=logins), \
                     patch.object(energy, 'get_devices_with_channels', side_effect=discoveries), \
                     patch.object(energy.os.path, 'exists', return_value=False), \
                     patch.object(energy.os, 'remove') as remove, \
                     patch.object(energy.time, 'sleep', side_effect=[None, StopLoop]) as sleep, \
                     patch.object(energy, 'poll_and_store') as poll, \
                     patch.object(energy, 'compact_reading_journal', return_value=False), \
                     patch.object(energy, 'write_poller_status') as status:
                    with self.assertRaises(StopLoop):
                        energy.run_continuous()
                poll.assert_called_once_with(client, ['device'])
                remove.assert_not_called()
                self.assertGreaterEqual(sleep.call_args_list[0].args[0], 30)
                self.assertTrue(status.call_args.args[0])

    def test_repeated_failure_is_rate_limited_and_never_polls(self):
        with patch.object(energy, 'login_vue', side_effect=TimeoutError('network')) as login, \
             patch.object(energy.os.path, 'exists', return_value=False), \
             patch.object(energy.time, 'sleep', side_effect=[None, StopLoop]), \
             patch.object(energy, 'poll_and_store') as poll, \
             patch.object(energy, 'write_poller_status') as status:
            with self.assertRaises(StopLoop):
                energy.run_continuous()
        self.assertEqual(login.call_count, 2)
        poll.assert_not_called()
        self.assertIn('Connection retry failed', status.call_args.kwargs['error'])
