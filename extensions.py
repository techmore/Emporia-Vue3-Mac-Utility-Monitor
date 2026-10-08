"""Explicit built-in extension registry. No arbitrary plugin loading or background jobs."""
from flask import Blueprint, jsonify, request

import climate
import radon

CATALOG = ({'id': 'climate', 'name': 'House climate', 'status': 'experimental',
            'url': '/house', 'sources': ['aqara', 'home_assistant', 'manual']},
           {'id': 'radon', 'name': 'Radon history', 'status': 'not_connected',
            'url': '/radon', 'sources': ['ecosense', 'home_assistant', 'manual']})


def register_extensions(app, render, common) -> None:
    blueprint = Blueprint('extensions', __name__)

    @blueprint.get('/api/extensions')
    def catalog():
        return jsonify({'extensions': CATALOG})

    @blueprint.get('/house')
    def house():
        return render('{% include "house.html" %}', active_page='house', **common())

    @blueprint.get('/radon')
    def radon_history():
        sensors = radon.get_sensors()
        source = request.args.get('source', '')
        sensor_id = request.args.get('sensor_id', '')
        selected = next((sensor for sensor in sensors
                         if sensor['source'] == source and sensor['sensor_id'] == sensor_id), None)
        rows = radon.get_history(source, sensor_id) if selected else []
        return render('{% include "radon.html" %}', active_page='radon',
                      sensors=sensors, selected=selected, rows=rows, **common())

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
