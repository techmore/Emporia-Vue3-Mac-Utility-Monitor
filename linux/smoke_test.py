"""Opt-in GUI integration check using synthetic loopback data in temporary state.

Run under an existing graphical session: /usr/bin/python3 linux/smoke_test.py
No real collector is contacted and no user profile is modified.
"""
import http.server
import json
import os
from pathlib import Path
import tempfile
import threading


class Fixture(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/api/menu-summary':
            payload = {'version': 'test-fixture', 'online': True, 'current_watts': 123,
                       'cost_per_hour': .03, 'recorded_kwh': 4,
                       'collector_source_id': 'fixture', 'active_device_gid': 'fixture',
                       'breaker_slots': [], 'top_circuits': []}
        elif self.path.startswith('/api/circuit-history/'):
            payload = {'windows': [{'days': days, 'total_kwh': 2.5, 'total_cents': 50,
                                   'change_pct': None, 'series': [
                                       {'period': str(i), 'total_kwh': value}
                                       for i, value in enumerate([1, None, 2])
                                   ]} for days in (1, 7, 30)]}
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'<!doctype html><title>Isolated test fixture</title>')
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def log_message(self, *_):
        return


def main():
    with tempfile.TemporaryDirectory() as directory:
        for key in ('XDG_STATE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME'):
            path = Path(directory) / key
            path.mkdir()
            os.environ[key] = str(path)
        import energy_monitor as client
        from gi.repository import GLib
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        app = client.EnergyMonitor()
        app.set_application_id("org.techmore.EnergyMonitor.SmokeTest")
        app.origin = f'http://127.0.0.1:{server.server_port}'
        result = []
        def history():
            app.open_history('Fixture Circuit')
            return False
        def check():
            try:
                assert app.connected, 'Summary bridge did not connect'
                assert app.record['data']['current_watts'] == 123
                assert app.history_windows['Fixture Circuit'][1]['record']['data']['windows']
                result.append('passed')
            finally:
                app.quit()
            return False
        GLib.timeout_add_seconds(3, history)
        GLib.timeout_add_seconds(4, lambda: (app.refresh(), False)[1])
        GLib.timeout_add_seconds(5, lambda: (app.refresh(), False)[1])
        GLib.timeout_add_seconds(8, check)
        app.run(['energy-monitor', '--background'])
        server.shutdown()
        assert result == ['passed'], 'GUI integration failed'
        print('Summary bridge, native history, chart gaps and isolated cache: passed')


if __name__ == '__main__':
    main()
