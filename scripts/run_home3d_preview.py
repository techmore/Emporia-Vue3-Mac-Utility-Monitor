#!/usr/bin/env python3
"""Run only Home 3D on a copied, explicitly marked local preview database."""
import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--port', type=int, default=15045)
    parser.add_argument('--sensors-host', help='Read-only SSH destination for the existing SER8 collector')
    parser.add_argument('--container', default='energy-monitor')
    args = parser.parse_args()
    root = args.data.resolve()
    if not (root / 'energy.db').is_file() or not 1024 <= args.port <= 65535:
        parser.error('Use an existing copied preview database and an unprivileged port')
    os.environ['DB_PATH'] = str(root / 'energy.db')
    os.environ['POLLER_STATUS_FILE'] = str(root / 'preview-poller-status.json')
    os.environ.pop('ENERGY_PUBLIC_ORIGIN', None)
    code = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(code))
    from preview_sensor_sync import preview_root, sync_status
    preview_root()  # Refuse unmarked destinations before importing the application.
    os.chdir(root)
    from flask import jsonify, redirect, request

    import web

    @web.app.before_request
    def isolate_preview():
        if request.path == '/':
            return redirect('/home-3d')
        allowed = request.path in ('/home-3d', '/api/version', '/api/house-control/config',
                                   '/api/house-control/state') or request.path.startswith('/static/')
        if not allowed:
            return jsonify(error='This isolated preview only provides Home 3D. Production is unchanged.'), 404
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and not (
                request.path == '/api/house-control/config' and request.method == 'PUT'):
            return jsonify(error='Device controls and collector changes are disabled in this preview.'), 403
        return None

    @web.app.after_request
    def preview_response(response):
        if request.path == '/api/house-control/state' and response.status_code == 200 and args.sensors_host:
            payload = response.get_json()
            payload['sensor_sync'] = sync_status()
            if not payload['sensor_sync']['ok']:
                for row in payload['sensors']:
                    row['stale'] = True
            response.set_data(json.dumps(payload, allow_nan=False))
        if request.path == '/home-3d' and response.status_code == 200:
            mode = 'SER8 sensor observations refresh every 30 seconds.' if args.sensors_host else 'Sensor observations are a snapshot.'
            notice = ('<p role="status" style="padding:12px;background:var(--olive-100);color:var(--text)">'
                      '<strong>LOCAL PREVIEW</strong> - ' + mode + ' Circuit history is a snapshot. '
                      'Floor plans and placements stay on this Mac; production is unchanged.</p>')
            response.set_data(response.get_data(as_text=True).replace(
                '<div class="page home3d">', '<div class="page home3d">' + notice, 1))
        return response

    worker = None
    def stop(_signum, _frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    try:
        if args.sensors_host:
            with open(root / 'sensor-sync.log', 'a') as log:
                worker = subprocess.Popen([
                    sys.executable, '-u', str(code / 'preview_sensor_sync.py'),
                    '--host', args.sensors_host, '--container', args.container,
                ], cwd=root, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        web.app.run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False)
    finally:
        if worker is not None:
            worker.terminate()
            try:
                worker.wait(timeout=25)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()


if __name__ == '__main__':
    main()
