"""Explicit built-in extension registry. No arbitrary plugin loading or background jobs."""
import asyncio
import hmac
import os

from flask import Blueprint, jsonify, request

import climate
import kasa_history
import kasa_monitor
import radon

CATALOG = ({'id': 'climate', 'name': 'House climate', 'status': 'experimental',
            'url': '/house', 'sources': ['aqara', 'home_assistant', 'manual']},
           {'id': 'radon', 'name': 'Radon history', 'status': 'not_connected',
            'url': '/radon', 'sources': ['ecosense', 'home_assistant', 'manual']})


def register_extensions(app, render, common) -> None:
    blueprint = Blueprint('extensions', __name__)

    @blueprint.after_request
    def protect_probe_response(response):
        if request.path.startswith('/api/kasa/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @blueprint.get('/api/extensions')
    def catalog():
        return jsonify({'extensions': CATALOG})

    @blueprint.route('/api/kasa/devices', methods=['GET', 'POST'])
    def kasa_devices():
        if request.method == 'GET':
            return jsonify({'devices': kasa_history.get_devices()})
        data = request.get_json()
        if set(data) - {'host', 'label'} or not isinstance(data.get('host'), str):
            return jsonify({'error': 'Supply only a device host and label; credentials are not stored'}), 400
        try:
            identifier = kasa_history.register_device(data['host'], data.get('label'))
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        return jsonify({'id': identifier}), 201

    @blueprint.delete('/api/kasa/devices/<identifier>')
    def remove_kasa_device(identifier):
        if len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
            return jsonify({'error': 'Device not found'}), 404
        if not kasa_history.remove_device(identifier):
            return jsonify({'error': 'Device not found'}), 404
        return jsonify({'removed': True})

    @blueprint.post('/api/kasa/probe')
    def kasa_probe():
        data = request.get_json()
        host = data.get('host')
        if not isinstance(host, str) or len(host) > 45:
            return jsonify({'error': 'Select one private IPv4 device address'}), 400
        try:
            result = asyncio.run(kasa_monitor.probe(
                host, data.get('username'), data.get('password'),
            ))
            response = jsonify(result)
        except ValueError:
            return jsonify({'error': 'Invalid address or credentials'}), 400
        except TimeoutError:
            return jsonify({'error': 'Device query timed out; state is unknown'}), 504
        except ImportError:
            return jsonify({'error': 'Kasa dependency is unavailable; rebuild this installation'}), 503
        except Exception as exc:
            return jsonify({'error': 'Device query failed; state is unknown',
                            'error_type': type(exc).__name__}), 502
        response.headers['Cache-Control'] = 'no-store'
        return response

    @blueprint.get('/house')
    def house():
        return render('{% include "house.html" %}', active_page='house', **common())

    @blueprint.get('/radon')
    def radon_history():
        try:
            days = int(request.args.get('days', '7'))
        except ValueError:
            return jsonify({'error': 'Invalid history window'}), 400
        if days not in (1, 7, 30, 365):
            return jsonify({'error': 'Invalid history window'}), 400
        sensors = radon.get_sensors()
        source = request.args.get('source', '')
        sensor_id = request.args.get('sensor_id', '')
        selected = next((sensor for sensor in sensors
                         if sensor['source'] == source and sensor['sensor_id'] == sensor_id), None)
        if not source and not sensor_id and sensors:
            selected = sensors[0]
            source, sensor_id = selected['source'], selected['sensor_id']
        rows = radon.get_history(source, sensor_id, days=days) if selected else []
        return render('{% include "radon.html" %}', active_page='radon',
                      sensors=sensors, selected=selected, rows=rows, days=days,
                      radon_cache=radon.get_cache_status(),
                      chart=radon.hourly_chart(rows, days=days), **common())

    @blueprint.post('/api/radon/readings')
    def radon_readings():
        try:
            return jsonify(radon.ingest_observations(request.get_json().get('observations')))
        except (ValueError, TypeError) as exc:
            return jsonify({'error': str(exc)}), 400

    @blueprint.get('/api/sync/radon')
    def radon_changes():
        token = os.environ.get('ENERGY_SYNC_TOKEN', '')
        if len(token) < 32:
            return jsonify({'error': 'History sync is not configured'}), 503
        authorization = request.headers.get('Authorization', '')
        if not hmac.compare_digest(authorization.encode('utf-8'),
                                   ('Bearer ' + token).encode('utf-8')):
            return jsonify({'error': 'History sync authorization required'}), 401
        try:
            expected_generation = request.args.get('generation_id')
            if expected_generation:
                identity = radon.get_changes(0, 1)
                if request.args.get('source_id') != identity['source_id']:
                    return jsonify({'error': 'Collector identity changed; fresh sync required'}), 409
                if expected_generation != identity['generation_id']:
                    return jsonify({'error': 'Radon checkpoint changed', 'reset_required': True,
                                    'source_id': identity['source_id']}), 409
            page = radon.get_changes(int(request.args.get('after', '0')),
                                     int(request.args.get('limit', '500')))
            if expected_generation and expected_generation != page['generation_id']:
                return jsonify({'error': 'Radon checkpoint changed; retry'}), 409
            expected_source = request.args.get('source_id')
            if expected_source and expected_source != page['source_id']:
                return jsonify({'error': 'Collector identity changed; fresh sync required'}), 409
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400
        response = jsonify(page)
        response.headers['Cache-Control'] = 'no-store'
        return response

    @blueprint.get('/api/climate/replay')
    def replay():
        try:
            return jsonify(climate.get_replay(int(request.args.get('days', '1')),
                                             demo=request.args.get('demo') == '1'))
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400

    @blueprint.post('/api/climate/readings')
    def readings():
        try:
            return jsonify(climate.ingest_observations(request.get_json().get('observations')))
        except (ValueError, TypeError) as exc:
            return jsonify({'error': str(exc)}), 400

    @blueprint.post('/api/climate/placement')
    def placement():
        data = request.get_json()
        if not all(isinstance(data.get(key), str) for key in ('source', 'sensor_id')):
            return jsonify({'error': 'source and sensor_id are required strings'}), 400
        if data.get('room_id') is not None and not isinstance(data['room_id'], str):
            return jsonify({'error': 'room_id must be a string or null'}), 400
        try:
            climate.place_sensor(data['source'], data['sensor_id'], data.get('room_id'))
            return jsonify({'ok': True})
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400

    app.register_blueprint(blueprint)

HOUSE_CSS = """
.house-lab {max-width:1250px;margin:auto;padding-bottom:40px;}
.house-head {display:flex;align-items:center;justify-content:space-between;margin:32px 0 22px;gap:16px;}
.house-head h1 {font-family:'Instrument Serif',serif;font-size:2.8rem;margin:8px 0;}
.house-head p,.house-caption,.house-model>p,.house-section span {color:var(--text-light);font-size:.85rem;line-height:1.6;}
.house-eyebrow {font-size:.7rem;letter-spacing:.16em;color:var(--accent);font-weight:700;}
.house-badge {border:1px solid var(--border);padding:6px 12px;border-radius:30px;font-size:.75rem;}
.house-toolbar {display:flex;align-items:end;gap:14px;flex-wrap:wrap;margin-bottom:16px;}
.house-lab label {display:flex;flex-direction:column;gap:6px;font-size:.75rem;color:var(--text-light);}
.house-lab select,.house-lab input[type=number],.house-lab button {background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:7px;padding:9px 12px;font:inherit;}
.house-lab button {cursor:pointer;}
.house-lab button:hover {background:var(--surface2);}
.house-notice {border-left:3px solid var(--accent);padding:10px 14px;background:var(--surface);font-size:.82rem;line-height:1.6;}
.house-grid {display:grid;grid-template-columns:minmax(0,2fr) minmax(260px,1fr);gap:18px;margin:18px 0;}
.house-lab .card {padding:22px;}
.house-section {display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px;}
.house-section h2 {font-family:'Instrument Serif',serif;font-size:1.35rem;margin:0;}
#house-plan {width:100%;display:block;background:var(--surface);}
#house-plan .room {stroke:var(--border);stroke-width:3;}
#house-plan text {fill:var(--text);font-family:Inter,sans-serif;}
#house-plan .room-name {font-size:17px;}
#house-plan .room-temp {font-size:26px;font-weight:600;}
#house-plan .sensor-dot {fill:var(--accent);}
.house-replay {display:grid;grid-template-columns:auto 1fr;gap:12px;align-items:center;margin-top:18px;}
.house-replay input {width:100%;accent-color:var(--accent);}
.house-replay output {grid-column:1/-1;font-size:.85rem;color:var(--text-light);}
.house-sensor {border-top:1px solid var(--border);padding:13px 0;}
.house-sensor strong {display:block;font-size:.9rem;}
.house-sensor .sensor-reading {margin:6px 0;font-size:.8rem;color:var(--text-light);}
.house-sensor select {width:100%;font-size:.8rem;}
#house-empty p {font-size:.85rem;line-height:1.7;color:var(--text-light);}
.house-energy,.house-model {margin-top:18px;}
#house-power {width:100%;height:100px;display:block;}
.house-inputs {display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:15px;margin:22px 0;}
.house-inputs input,.house-inputs select {width:100%;box-sizing:border-box;}
#model-result {background:var(--surface2);padding:16px;border-radius:8px;color:var(--text);}
@media(max-width:850px) {.house-grid{grid-template-columns:1fr;}.house-head h1{font-size:2rem;}.house-head{align-items:start;}.house-section{align-items:start;flex-direction:column;}}
"""
