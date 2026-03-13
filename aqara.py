"""
aqara.py — Aqara Cloud OpenAPI client skeleton
================================================
Status: PLACEHOLDER — Aqara developer signup is temporarily unavailable.

When developer.aqara.com sign-up reopens:
  1. Create an app → get APP_ID, APP_KEY, KEY_ID
  2. Run authorize_url() to get the OAuth URL, open it in a browser
  3. Exchange the returned authorization code for ACCESS_TOKEN + REFRESH_TOKEN
  4. Store tokens in settings.json under key "aqara"
  5. Call get_sensors() to pull live temp/humidity readings

API reference:
  https://opendoc.aqara.com/en/docs/developmanual/cloudDevelopment/openApiV3.html

Regions:
  USA:    https://aiot-open-3rd.aqara.com
  EU:     https://aiot-open-3rd-ger.aqara.com
  China:  https://aiot-open-3rd.aqara.cn
"""

import hashlib
import hmac
import json
import os
import time
import uuid
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

SETTINGS_FILE = Path(__file__).parent / "settings.json"

# Aqara API base URL by region (change if needed)
API_BASE = "https://aiot-open-3rd.aqara.com"

# Attribute resource IDs for temp/humidity sensors (Aqara TVOC/TH sensors)
RESOURCE_TEMPERATURE = "0.1.85"   # Temperature in 0.01 °C units → divide by 100
RESOURCE_HUMIDITY    = "0.2.85"   # Relative humidity in 0.01 % → divide by 100
RESOURCE_BATTERY     = "8.0.2005" # Battery percentage


# ── Settings helpers ───────────────────────────────────────────────────────────

def _load_aqara_config() -> dict:
    """Return aqara section from settings.json, or empty dict."""
    try:
        raw = json.loads(SETTINGS_FILE.read_text())
        return raw.get("aqara", {})
    except Exception:
        return {}


def _save_aqara_config(cfg: dict):
    """Merge cfg into the aqara section of settings.json."""
    try:
        raw = json.loads(SETTINGS_FILE.read_text()) if SETTINGS_FILE.exists() else {}
    except Exception:
        raw = {}
    raw.setdefault("aqara", {}).update(cfg)
    import energy

    energy._write_json_file(SETTINGS_FILE, raw)


def is_configured() -> bool:
    """Return True if we have the minimum credentials to make API calls."""
    cfg = _load_aqara_config()
    return bool(cfg.get("app_id") and cfg.get("app_key") and cfg.get("access_token"))


# ── Auth helpers ───────────────────────────────────────────────────────────────

def _sign(app_id: str, app_key: str, key_id: str, nonce: str, timestamp: str) -> str:
    """
    Aqara OpenAPI v3 HMAC-SHA256 signature.
    sign = HMAC-SHA256(
        key   = AppKey,
        data  = "Appid={app_id}&Keyid={key_id}&Nonce={nonce}&Time={timestamp}"
    ).lower()
    """
    payload = f"Appid={app_id}&Keyid={key_id}&Nonce={nonce}&Time={timestamp}"
    return hmac.new(app_key.encode(), payload.encode(), hashlib.sha256).hexdigest().lower()


def _headers(cfg: dict) -> dict:
    """Build the auth headers for an Aqara API request."""
    app_id  = cfg["app_id"]
    app_key = cfg["app_key"]
    key_id  = cfg["key_id"]
    nonce   = uuid.uuid4().hex[:16]
    ts      = str(int(time.time() * 1000))
    sign    = _sign(app_id, app_key, key_id, nonce, ts)
    return {
        "Content-Type": "application/json",
        "Appid":        app_id,
        "Keyid":        key_id,
        "Nonce":        nonce,
        "Time":         ts,
        "Sign":         sign,
        "Accesstoken":  cfg.get("access_token", ""),
        "Lang":         "en",
    }


# ── OAuth flow (to be wired into a Flask route when ready) ────────────────────

def authorize_url(cfg: dict, redirect_uri: str = "http://localhost:5001/api/aqara/callback") -> str:
    """
    Return the URL the user opens in a browser to link their Aqara account.
    After approval, Aqara redirects to redirect_uri?code=<AUTH_CODE>&state=<STATE>.
    Pass the auth_code to exchange_token().
    """
    state = uuid.uuid4().hex
    return (
        f"https://account.aqara.com/oauth2/auth"
        f"?client_id={cfg['app_id']}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&scope=0"
        f"&state={state}"
    )


def exchange_token(auth_code: str, cfg: dict) -> dict:
    """
    Exchange an OAuth authorization code for access + refresh tokens.
    Stores tokens in settings.json and returns the response dict.

    POST /open/api  intent=config.auth.getAuthToken
    """
    import urllib.request
    body = json.dumps({
        "intent": "config.auth.getAuthToken",
        "data": {
            "authCode":   auth_code,
            "accessType": 0,   # 0 = authorization_code
        }
    }).encode()
    req = urllib.request.Request(
        f"{API_BASE}/open/api",
        data=body,
        headers=_headers(cfg),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
    if result.get("code") == 0:
        token_data = result.get("result", {})
        _save_aqara_config({
            "access_token":  token_data.get("accessToken"),
            "refresh_token": token_data.get("refreshToken"),
            "token_expires": int(time.time()) + token_data.get("expiresIn", 7776000),
        })
    return result


def refresh_token(cfg: dict) -> dict:
    """Refresh an expired access token using the stored refresh token."""
    import urllib.request
    body = json.dumps({
        "intent": "config.auth.refreshToken",
        "data": {"refreshToken": cfg["refresh_token"]}
    }).encode()
    req = urllib.request.Request(
        f"{API_BASE}/open/api",
        data=body,
        headers=_headers(cfg),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
    if result.get("code") == 0:
        token_data = result.get("result", {})
        _save_aqara_config({
            "access_token":  token_data.get("accessToken"),
            "refresh_token": token_data.get("refreshToken"),
            "token_expires": int(time.time()) + token_data.get("expiresIn", 7776000),
        })
    return result


# ── Device queries ─────────────────────────────────────────────────────────────

def get_devices(cfg: dict) -> list[dict]:
    """
    Fetch all devices associated with the account.
    Returns list of dicts with keys: did, model, name, state, parentId, etc.
    """
    import urllib.request
    body = json.dumps({
        "intent": "query.device.info",
        "data":   {"pageNum": 1, "pageSize": 50},
    }).encode()
    req = urllib.request.Request(
        f"{API_BASE}/open/api",
        data=body,
        headers=_headers(cfg),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
    return result.get("result", {}).get("data", [])


def get_sensors(cfg: dict | None = None) -> list[dict]:
    """
    Return temp/humidity readings for all TH-type sensors.

    Each entry:
        {
          "did":         "<device id>",
          "name":        "Living Room",
          "model":       "lumi.sensor_ht.agl02",
          "temperature": 21.5,   # °C  (None if unavailable)
          "humidity":    48.2,   # %RH (None if unavailable)
          "battery":     87,     # %   (None if unavailable)
          "online":      True,
        }

    Returns empty list if not configured or API call fails.
    """
    if cfg is None:
        cfg = _load_aqara_config()
    if not is_configured():
        return []

    try:
        import urllib.request

        # Step 1: get device list
        devices = get_devices(cfg)

        # Step 2: filter to TH/TVOC sensors (model contains "sensor_ht" or "sensor_air")
        th_devices = [
            d for d in devices
            if any(kw in d.get("model", "").lower()
                   for kw in ("sensor_ht", "sensor_air", "temphumid", "th"))
        ]

        if not th_devices:
            return []

        # Step 3: batch-query resource attributes
        resources = []
        for d in th_devices:
            for rid in (RESOURCE_TEMPERATURE, RESOURCE_HUMIDITY, RESOURCE_BATTERY):
                resources.append({"subjectId": d["did"], "resourceId": rid})

        body = json.dumps({
            "intent": "query.resource.value",
            "data":   {"resources": resources},
        }).encode()
        req = urllib.request.Request(
            f"{API_BASE}/open/api",
            data=body,
            headers=_headers(cfg),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())

        values = {
            (r["subjectId"], r["resourceId"]): r.get("value")
            for r in result.get("result", [])
        }

        sensors = []
        for d in th_devices:
            did = d["did"]
            raw_t = values.get((did, RESOURCE_TEMPERATURE))
            raw_h = values.get((did, RESOURCE_HUMIDITY))
            raw_b = values.get((did, RESOURCE_BATTERY))
            sensors.append({
                "did":         did,
                "name":        d.get("name", did),
                "model":       d.get("model", ""),
                "temperature": round(int(raw_t) / 100, 1) if raw_t is not None else None,
                "humidity":    round(int(raw_h) / 100, 1) if raw_h is not None else None,
                "battery":     int(raw_b) if raw_b is not None else None,
                "online":      d.get("state", 0) == 1,
            })
        return sensors

    except Exception as exc:
        print(f"[aqara] get_sensors error: {exc}")
        return []
