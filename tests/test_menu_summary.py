import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('swift'), 'Swift runtime required')
class MenuSummaryTests(unittest.TestCase):
    def test_numeric_snake_case_cost_decodes_and_slots_keep_their_identities(self):
        source = (Path(__file__).resolve().parents[1]
                  / 'EnergyMonitorApp/Sources/MenuPopover.swift').read_text()
        models = source[source.index('struct MenuCircuit'):source.index('struct CircuitBucket')]
        payload = {'version': '2.3.5', 'online': True, 'cost_24h': 4.25,
                   'month_cost': 12.5, 'top_circuits': [], 'panel_label': 'Service Panel',
                   'panel_slots': 2, 'breaker_slots': [
                       {'slot': 2, 'channel_name': 'two', 'display_name': 'Second', 'poles': 1},
                       {'slot': 1, 'channel_name': 'one', 'display_name': 'First', 'poles': 2}]}
        harness = 'import Foundation\n' + models + '''
let decoder = JSONDecoder()
decoder.keyDecodingStrategy = .convertFromSnakeCase
let summary = try decoder.decode(MenuSummary.self, from: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])))
precondition(summary.cost24h == 4.25)
precondition(summary.monthCost == 12.5)
let slots = summary.breakerSlots.sorted { $0.slot < $1.slot }
precondition(slots.map { $0.slot } == [1, 2])
precondition(slots.map { $0.channelName! } == ["one", "two"])
precondition(slots.map { $0.poles } == [2, 1])
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'main.swift'
            script.write_text(harness)
            data = root / 'summary.json'
            data.write_text(json.dumps(payload))
            result = subprocess.run(['swift', str(script), str(data)],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
