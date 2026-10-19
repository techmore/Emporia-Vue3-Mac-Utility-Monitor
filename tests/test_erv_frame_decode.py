import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('erv_decode', Path(__file__).parents[1] / 'scripts/erv_frame_decode.py')
erv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(erv)

# Example frame from the community protocol write-up (checksum 0x4a, end byte 0x04).
KNOWN = bytes.fromhex('0110110104400050004a04')


class ErvFrameTests(unittest.TestCase):
    def test_checksum_matches_published_example(self):
        self.assertEqual(erv.checksum(KNOWN[:-2]), 0x4A)

    def test_valid_and_corrupt_frames(self):
        got = list(erv.frames(KNOWN + KNOWN[:-2] + b'\x00\x04'))
        self.assertEqual([(ok, why) for _, ok, why in got], [(True, ''), (False, 'bad checksum')])

    def test_resync_after_noise_and_truncation(self):
        got = list(erv.frames(b'\xff\x00' + KNOWN + KNOWN[:6]))
        self.assertEqual([(ok, why) for _, ok, why in got], [(True, ''), (False, 'truncated')])

    def test_extracts_hex_from_esphome_log_lines(self):
        line = '[10:00:00][D][uart_debug:158]: <<< 01:10:11:01:04:40:00:50:00:4a:04'
        self.assertEqual(erv.hex_bytes(line), KNOWN)
        self.assertIn('register write', erv.describe(KNOWN) if False else erv.describe(KNOWN[:5] + b'\x40\x00\x50\x00' + KNOWN[-2:]))


if __name__ == '__main__':
    unittest.main()
