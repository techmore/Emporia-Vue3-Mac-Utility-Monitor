"""Private floor-plan configuration and read-only circuit-to-room projection."""
import fcntl
import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import aqara_local
import climate
import energy
import radon
from runtime_store import write_private_json

UPSTREAM_COMMIT = "1ad70a26197c0503b5af7932f0b5c8a8d3c7a6f9"
DEFAULT_PLAN = {
    "name": "Example home - replace with your floor plan",
    "rooms": [
        {"id": "living", "name": "Living room", "rect": [0, 0, 5, 4], "floor": "oak"},
        {"id": "kitchen", "name": "Kitchen", "rect": [5, 0, 3, 4], "floor": "tile"},
        {"id": "bedroom", "name": "Bedroom", "rect": [0, 4, 5, 3], "floor": "walnut"},
        {"id": "utility", "name": "Utility", "rect": [5, 4, 3, 3], "floor": "stone"},
    ],
    "openings": [], "furniture": [],
    "wallHeight": 2.6, "interiorWallHeight": 0.8,
    "camera": {"azimuth": 34, "elevation": 47},
}
FURNITURE = frozenset(("rug", "sofa", "armchair", "coffeeTable", "tv", "sideboard",
                      "diningSet", "island", "kitchen", "fridge", "bed", "nightstand",
                      "dresser", "plant", "vanity", "bathtub", "toilet", "desk",
                      "wardrobe", "bookshelf"))


class RevisionConflict(ValueError):
    """A different client has saved since this client loaded its configuration."""


def _path() -> Path:
    return Path(energy.DB_PATH).resolve().with_name("house-control.json")


def _text(value, field: str, limit: int = 120) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} must be a nonempty string of at most {limit} characters")
    return value


def _number(value, field: str, low: float, high: float) -> float:
    if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number between {low} and {high}")
    return value


def _point(value, field: str) -> list:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{field} needs two coordinates")
    return [_number(v, field, -100, 100) for v in value]


def validate_plan(raw: dict) -> dict:
    """Accept bounded house-control geometry; omit platform/device/scene credentials."""
    if not isinstance(raw, dict):
        raise ValueError("Plan must be a JSON object")
    rooms = raw.get("rooms")
    if not isinstance(rooms, list) or not 1 <= len(rooms) <= 64:
        raise ValueError("Plan requires 1 to 64 rooms")
    plan = {"name": _text(raw.get("name", "Home"), "Plan name"), "rooms": [],
            "openings": [], "furniture": []}
    ids = set()
    for room in rooms:
        if not isinstance(room, dict):
            raise ValueError("Each room must be an object")
        identifier = _text(room.get("id"), "Room id", 80)
        if identifier in ids:
            raise ValueError("Room ids must be unique")
        ids.add(identifier)
        rect = room.get("rect")
        if not isinstance(rect, list) or len(rect) != 4:
            raise ValueError("Room rect needs [x, z, width, depth] in meters")
        rect = _point(rect[:2], "Room position") + [
            _number(v, "Room dimension", 0.5, 50) for v in rect[2:]]
        floor = room.get("floor", "oak")
        if floor not in ("oak", "walnut", "tile", "stone"):
            raise ValueError("Unknown floor material")
        plan["rooms"].append({"id": identifier, "name": _text(room.get("name", identifier),
                                                              "Room name"),
                              "rect": rect, "floor": floor})
    height = _number(raw.get("wallHeight", 2.6), "Wall height", 0.5, 6)
    plan["wallHeight"] = height
    plan["interiorWallHeight"] = _number(raw.get("interiorWallHeight", min(1.25, height)),
                                            "Interior wall height", 0.1, height)
    camera = raw.get("camera", {})
    if not isinstance(camera, dict):
        raise ValueError("Camera must be an object")
    plan["camera"] = {"azimuth": _number(camera.get("azimuth", 34), "Azimuth", -180, 180),
                      "elevation": _number(camera.get("elevation", 47), "Elevation", 20, 75)}
    for name in ("openings", "furniture"):
        items = raw.get(name, [])
        if not isinstance(items, list) or len(items) > 256:
            raise ValueError(f"{name} must contain at most 256 items")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"Each {name} item must be an object")
            if name == "openings":
                start = _point(item.get("from"), "Opening from")
                end = _point(item.get("to"), "Opening to")
                kind = item.get("type", "door")
                if kind not in ("door", "window", "open") or start == end or (
                        start[0] != end[0] and start[1] != end[1]):
                    raise ValueError("Openings must have a supported type and axis-aligned length")
                sill = _number(item.get("sill", min(0.85, height / 3)), "Window sill", 0, height)
                head = _number(item.get("head", min(2.1, height)), "Window head", sill, height)
                if kind == "window" and sill == head:
                    raise ValueError("Window head must be above its sill")
                clean = {"type": kind, "from": start, "to": end, "sill": sill, "head": head}
            else:
                if item.get("type") not in FURNITURE:
                    raise ValueError("Unsupported furniture type")
                clean = {"type": item["type"], "pos": _point(item.get("pos"), "Furniture position")}
                for key in ("width", "height"):
                    if key in item:
                        clean[key] = _number(item[key], key, 0.3, 10)
                for key in ("rot", "y"):
                    if key in item:
                        clean[key] = _number(item[key], key, -360 if key == "rot" else 0,
                                             360 if key == "rot" else 6)
                if "size" in item:
                    if not isinstance(item["size"], list) or len(item["size"]) != 2:
                        raise ValueError("Furniture size needs two dimensions")
                    clean["size"] = [_number(v, "Furniture size", 0.5, 10) for v in item["size"]]
                if "color" in item:
                    if not isinstance(item["color"], str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", item["color"]):
                        raise ValueError("Furniture color must be #RRGGBB")
                    clean["color"] = item["color"]
                for key in ("chairs", "stools"):
                    if key in item:
                        if type(item[key]) is not int or not 0 <= item[key] <= 8:
                            raise ValueError("Furniture seat count must be 0 to 8")
                        clean[key] = item[key]
                if "upper" in item:
                    if type(item["upper"]) is not bool:
                        raise ValueError("Furniture upper must be boolean")
                    clean["upper"] = item["upper"]
            plan[name].append(clean)
    return plan


def _revision(config: dict) -> str:
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def load_config() -> dict:
    try:
        config = json.loads(_path().read_text())
    except FileNotFoundError:
        config = {"plan": DEFAULT_PLAN, "bindings": [], "example": True}
    config.setdefault("sensors", [])
    return {**config, "revision": _revision(config)}


def sensor_catalog(now: datetime | None = None) -> list[dict]:
    """Project stored observations only; never start discovery or vendor requests."""
    now = now or datetime.now(timezone.utc)
    rows = []

    def add(source, identifier, name, stamp, metrics, max_age, online=True):
        try:
            observed = datetime.fromisoformat(stamp)
            age = (now - observed).total_seconds() if observed.tzinfo else float('inf')
        except (TypeError, ValueError):
            age = float('inf')
        rows.append({"source": source, "sensor_id": identifier, "name": name or identifier,
                     "timestamp": stamp, "stale": not online or not -300 <= age <= max_age,
                     "available": True, "metrics": metrics, "placement": None})

    def number(value, low, high):
        return value if type(value) in (float, int) and low <= value <= high else None

    def temperature(value):
        value = number(value, -80, 80)
        return value * 9 / 5 + 32 if value is not None else None

    for row in aqara_local.get_sensors():
        add('aqara_local', row['did'], row['name'], row['observed_at'], {
            'temperature_f': temperature(row['temperature']),
            'humidity_pct': number(row['humidity'], 0, 100),
            'battery_pct': number(row['battery'], 0, 100),
        }, 180, row['online'])
    for row in climate.get_latest_sensors():
        add('climate:' + row['source'], row['sensor_id'], row['name'], row['timestamp'], {
            'temperature_f': temperature(row['temperature_c']),
            'humidity_pct': number(row['humidity_pct'], 0, 100),
            'battery_pct': number(row['battery_pct'], 0, 100),
        }, 180)
    for row in radon.get_latest_readings():
        value = number(row['radon_bq_m3'], 0, 1e9)
        add('radon:' + row['source'], row['sensor_id'], row['name'], row['timestamp'], {
            'radon_pci_l': value / 37 if value is not None else None,
        }, 10800)
    return rows


def _validate_sensors(raw, plan, previous) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > 256:
        raise ValueError('Sensor placements must contain at most 256 items')
    known = {(row['source'], row['sensor_id']) for row in sensor_catalog()}
    known.update((row['source'], row['sensor_id']) for row in previous)
    rooms = {room['id']: room for room in plan['rooms']}
    seen, clean = set(), []
    for row in raw:
        if not isinstance(row, dict) or set(row) != {'source', 'sensor_id', 'room_id', 'position'}:
            raise ValueError('Each sensor placement needs source, sensor_id, room_id and position')
        source = _text(row['source'], 'Sensor source')
        identifier = _text(row['sensor_id'], 'Sensor id', 256)
        identity = (source, identifier)
        if identity not in known or identity in seen:
            raise ValueError('Place each recorded sensor at most once')
        room_id = _text(row['room_id'], 'Room id', 80)
        if room_id not in rooms:
            raise ValueError('Place sensors in a known room')
        position = row['position']
        if not isinstance(position, list) or len(position) != 2:
            raise ValueError('Sensor position needs [x, z] in meters')
        position = [_number(value, 'Sensor coordinate', -100, 150) for value in position]
        x, z, width, depth = rooms[room_id]['rect']
        if not (x <= position[0] <= x + width and z <= position[1] <= z + depth):
            raise ValueError('Sensor position must be inside its assigned room')
        seen.add(identity)
        clean.append({'source': source, 'sensor_id': identifier,
                      'room_id': room_id, 'position': position})
    return clean


def save_config(data: dict, known_circuits: dict[str, set[str]] | None = None) -> dict:
    if not isinstance(data, dict) or set(data) not in (
            {"plan", "bindings", "revision"}, {"plan", "bindings", "revision", "sensors"}):
        raise ValueError("Supply plan, bindings, and the loaded revision")
    plan = validate_plan(data["plan"])
    bindings = data["bindings"]
    if not isinstance(bindings, list) or len(bindings) > 256:
        raise ValueError("Bindings must contain at most 256 circuits")
    room_ids = {r["id"] for r in plan["rooms"]}
    seen = set()
    clean = []
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {"device_gid", "channel_name", "room_id"}:
            raise ValueError("Each binding needs a panel, circuit, and room")
        gid = _text(binding["device_gid"], "Panel id")
        channel = _text(binding["channel_name"], "Circuit name", 256)
        room = _text(binding["room_id"], "Room id", 80)
        if room not in room_ids or channel in energy.META_CHANNELS or (gid, channel) in seen:
            raise ValueError("Map each non-main circuit once to a known room")
        seen.add((gid, channel))
        clean.append({"device_gid": gid, "channel_name": channel, "room_id": room})
    lock = _path().with_suffix(".lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        current = load_config()
        if data["revision"] != current["revision"]:
            raise RevisionConflict("Floor plan changed in another tab; reload before saving")
        # Older mapping clients must never erase placements by omitting the new field.
        sensors = _validate_sensors(data.get('sensors', current['sensors']), plan, current['sensors'])
        known = {}
        devices = set(known_circuits) if known_circuits is not None else set(energy.get_known_devices())
        for binding in clean:
            if binding in current["bindings"]:
                continue
            gid = binding["device_gid"]
            if gid not in devices:
                raise ValueError("Unknown panel")
            if gid not in known:
                known[gid] = known_circuits[gid] if known_circuits is not None else {
                    r["channel_name"] for r in energy.get_latest(gid)}
            if binding["channel_name"] not in known[gid]:
                raise ValueError("Unknown circuit on the selected panel")
        write_private_json(_path(), {"plan": plan, "bindings": clean, "sensors": sensors,
                                     "example": plan == validate_plan(DEFAULT_PLAN)})
    return load_config()


def project_state(config: dict, menu: dict, summary: list[dict]) -> dict:
    """Project only the active panel; never attribute one circuit to multiple rooms."""
    totals = {r["channel_name"]: r for r in summary}
    gid = menu["active_device_gid"]
    rooms = {r["id"]: [] for r in config["plan"]["rooms"]}
    bindings = {b["channel_name"]: b["room_id"] for b in config["bindings"]
                if b["device_gid"] == gid}
    circuits = {}
    for slot in menu["breaker_slots"]:
        channel = slot["channel_name"]
        if not channel or channel in circuits:
            continue
        total = totals.get(channel, {})
        row = {"channel_name": channel, "display_name": slot["display_name"],
               "watts": slot["watts"] if menu["online"] else None,
               "kwh_24h": total.get("total_kwh"),
               "cost_24h": total["total_cents"] / 100 if total.get("total_cents") is not None else None,
               "room_id": bindings.get(channel), "missing": False}
        circuits[channel] = row
        if row["room_id"] in rooms:
            rooms[row["room_id"]].append(row)
    # Keep disappeared circuits visible so their saved room mappings can be removed.
    for channel, room_id in bindings.items():
        if channel in circuits:
            continue
        total = totals.get(channel, {})
        row = {"channel_name": channel, "display_name": channel, "watts": None,
               "kwh_24h": total.get("total_kwh"),
               "cost_24h": total["total_cents"] / 100 if total.get("total_cents") is not None else None,
               "room_id": room_id, "missing": True}
        circuits[channel] = row
        if room_id in rooms:
            rooms[room_id].append(row)
    result = []
    for room in config["plan"]["rooms"]:
        expected = [name for name, identifier in bindings.items() if identifier == room["id"]]
        rows = rooms[room["id"]]
        values = {key: sum(r[key] for r in rows) if expected and len(rows) == len(expected)
                  and all(r[key] is not None for r in rows) else None
                  for key in ("watts", "kwh_24h", "cost_24h")}
        result.append({"id": room["id"], "name": room["name"], "mapped_circuits": len(expected),
                       "reporting_circuits": sum(r["watts"] is not None for r in rows), **values})
    sensors = {(row['source'], row['sensor_id']): row for row in sensor_catalog()}
    for placement in config.get('sensors', []):
        key = (placement['source'], placement['sensor_id'])
        if key not in sensors:
            sensors[key] = {'source': key[0], 'sensor_id': key[1], 'name': key[1],
                            'timestamp': None, 'stale': True, 'available': False,
                            'metrics': {}, 'placement': None}
        sensors[key]['placement'] = placement
    return {"active_device_gid": gid, "online": menu["online"], "last_reading": menu["last_reading"],
            "current_watts": menu["current_watts"] if menu["online"] else None, "rooms": result,
            "circuits": list(circuits.values()), "sensors": list(sensors.values()),
            "revision": config["revision"],
            "other_panel_bindings": sum(b["device_gid"] != gid for b in config["bindings"])}


HOUSE_CONTROL_CSS = """
.home3d {max-width:1400px;margin:auto;padding-bottom:32px;}
.home3d-head,.home3d-toolbar {display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;}
.home3d-head {margin:20px 0 12px;}
.home3d-head h1 {font-family:'Instrument Serif',serif;font-size:2rem;margin:0;}
.home3d p,.home3d-caption,.home3d small {color:var(--text-light);font-size:.82rem;line-height:1.5;}
.home3d-badge,.home3d-label {background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:5px 9px;font-size:.75rem;}
.home3d button,.home3d select,.home3d input {font:inherit;max-width:100%;}
.home3d button,.home3d select {background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:8px 12px;}
.home3d button {cursor:pointer;}
.home3d button:disabled {opacity:.5;cursor:default;}
.home3d label {display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:.85rem;}
.home3d label select {min-width:0;flex:1;}
.home3d-toolbar {justify-content:flex-start;margin:14px 0;}
.home3d-layout {display:grid;grid-template-columns:minmax(0,2fr) minmax(230px,1fr);gap:14px;}
.home3d-stage {height:480px;position:relative;overflow:hidden;padding:0;}
#home3d-canvas {display:block;width:100%;height:100%;touch-action:none;}
#home3d-labels {position:absolute;inset:0;pointer-events:none;overflow:hidden;}
.home3d-label {position:absolute;transform:translate(-50%,-50%);white-space:nowrap;max-width:200px;text-overflow:ellipsis;overflow:hidden;}
#home3d-render-error {position:absolute;inset:30% 10%;padding:16px;background:var(--surface);}
.home3d-rooms {padding:16px;}
.home3d-rooms h2 {font-size:1.1rem;margin:0;}
.home3d-room {display:grid;grid-template-columns:1fr auto;gap:6px;text-align:left;width:100%;margin:8px 0;}
.home3d-room small {grid-column:1/-1;}
.home3d-room[aria-pressed=true] {border-color:var(--olive-600);background:var(--olive-100);}
.home3d-setup {padding:16px;margin-top:14px;}
.home3d-setup summary {cursor:pointer;font-weight:600;}
.home3d-setup fieldset {border:0;padding:0;min-width:0;}
.home3d-editor-layout {display:grid;grid-template-columns:minmax(0,2fr) minmax(220px,1fr);gap:16px;margin:16px 0;}
.home3d-editor-canvas {display:block;width:100%;height:400px;background:var(--surface);border:1px solid var(--border);touch-action:none;}
.editor-room {fill:var(--olive-100);stroke:var(--olive-600);stroke-width:.06;}
.editor-name {font-size:.32px;text-anchor:middle;dominant-baseline:middle;fill:var(--text);pointer-events:none;}
.editor-furniture {fill:var(--stone-300);stroke:var(--text-light);stroke-width:.05;}
.editor-opening {stroke:var(--olive-800);stroke-width:.18;}
.editor-handle {fill:var(--olive-800);stroke:var(--surface);stroke-width:.04;cursor:nwse-resize;}
.selected .editor-room,.selected .editor-furniture {stroke:var(--olive-950);stroke-width:.12;}
.home3d-inspector label {margin:10px 0;}
.home3d-inspector input {width:110px;padding:6px;background:var(--surface);color:var(--text);border:1px solid var(--border);}
.editor-sensor {fill:var(--olive-800);stroke:var(--surface);stroke-width:.07;cursor:move;}
.selected .editor-sensor {stroke:var(--text);stroke-width:.12;}
#home3d-sensors,#home3d-mains {display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;}
.home3d-sensor {display:grid;gap:6px;border:1px solid var(--border);border-left:4px solid var(--olive-600);border-radius:8px;padding:12px;overflow-wrap:anywhere;}
.home3d-sensor-marker {border-left:4px solid var(--olive-600);font-size:.65rem;max-width:260px;}
[data-sensor-source^="radon:"] {border-left-color:var(--stone-500);}
[data-sensor-source^="climate:"] {border-left-color:var(--olive-950);}
.home3d-sensor-marker.stale {border-style:dashed;}
[data-editor=draw][aria-pressed=true] {background:var(--olive-200);}
#home3d-mappings {display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin:14px 0;}
#home3d-mappings select {max-width:55%;}
@media(max-width:760px) {.home3d-layout,.home3d-editor-layout{grid-template-columns:1fr;}.home3d-stage{height:350px;}.home3d-toolbar label{width:100%;}.home3d-label{font-size:.65rem;max-width:130px;}}
"""
