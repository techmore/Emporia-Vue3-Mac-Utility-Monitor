"""Client for Aqara's v3 cloud API, including account authorization and sensor reads."""

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# The launcher sets cwd to the runtime data directory, not the installed source directory.
SETTINGS_FILE = Path("settings.json")
API_BASES = {
    "US": "https://open-usa.aqara.com",
    "EU": "https://open-ger.aqara.com",
    "CN": "https://open-cn.aqara.com",
    "KR": "https://open-kr.aqara.com",
    "RU": "https://open-ru.aqara.com",
    "SG": "https://open-sg.aqara.com",
}
API_TIMEOUT_SECONDS = 15
TOKEN_REFRESH_MARGIN_SECONDS = 60
_RESOURCE_INFO_CACHE: dict[tuple[str, str], dict[str, str]] = {}


class AqaraError(RuntimeError):
    """A safe-to-display Aqara API or configuration error."""


def _read_settings() -> dict:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AqaraError(f"Could not read settings.json: {exc}") from exc
    if not isinstance(settings, dict):
        raise AqaraError("settings.json must contain a JSON object")
    return settings


def _load_aqara_config() -> dict:
    config = _read_settings().get("aqara", {})
    if not isinstance(config, dict):
        raise AqaraError("The aqara settings section must be a JSON object")
    return config


def _save_aqara_config(updates: dict) -> None:
    import energy

    settings = _read_settings()
    config = settings.setdefault("aqara", {})
    if not isinstance(config, dict):
        raise AqaraError("The aqara settings section must be a JSON object")
    config.update(updates)
    energy._write_json_file(SETTINGS_FILE, settings)


def save_app_config(data: dict) -> None:
    """Validate and save Aqara app credentials without overwriting other settings."""
    old = _load_aqara_config()
    app_id = str(data.get("app_id") or "").strip()
    key_id = str(data.get("key_id") or "").strip()
    app_key = str(data.get("app_key") or "").strip() or old.get("app_key", "")
    account = str(data.get("account") or "").strip()
    region = str(data.get("region") or old.get("region") or "US").upper()
    if not app_id or not key_id or not app_key or not account:
        raise AqaraError("App ID, Key ID, App Key, and Aqara account are required")
    if region not in API_BASES:
        raise AqaraError("Choose a supported Aqara server region")

    updated = {"app_id": app_id, "key_id": key_id, "app_key": app_key,
               "account": account, "region": region}
    # Tokens belong to a specific app/account pair; never reuse them after changing either.
    if any(old.get(key) != updated[key] for key in
           ("app_id", "key_id", "app_key", "account", "region")):
        updated.update({"access_token": "", "refresh_token": "", "token_expires_at": 0})
    _save_aqara_config(updated)


def is_configured() -> bool:
    config = _load_aqara_config()
    return bool(config.get("app_id") and config.get("key_id") and
                config.get("app_key") and config.get("access_token"))


def _sign(app_id: str, app_key: str, key_id: str, nonce: str, timestamp: str,
          access_token: str = "") -> str:
    """Build Aqara's v3 signature: lowercase ordered header string + AppKey, MD5."""
    fields = []
    if access_token:
        fields.append(f"Accesstoken={access_token}")
    fields.extend((f"Appid={app_id}", f"Keyid={key_id}", f"Nonce={nonce}",
                   f"Time={timestamp}"))
    payload = ("&".join(fields) + app_key).lower()
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _headers(config: dict, include_token: bool = True) -> dict:
    nonce = uuid.uuid4().hex[:16]
    timestamp = str(int(time.time() * 1000))
    token = config.get("access_token", "") if include_token else ""
    headers = {
        "Content-Type": "application/json",
        "Appid": config["app_id"],
        "Keyid": config["key_id"],
        "Nonce": nonce,
        "Time": timestamp,
        "Sign": _sign(config["app_id"], config["app_key"], config["key_id"],
                       nonce, timestamp, token),
        "Lang": "en",
    }
    if token:
        headers["Accesstoken"] = token
    return headers


def _api_url(config: dict) -> str:
    region = str(config.get("region", "US")).upper()
    try:
        return f"{API_BASES[region]}/v3.0/open/api"
    except KeyError as exc:
        raise AqaraError(f"Unsupported Aqara region: {region}") from exc


def _post_intent(config: dict, intent: str, data: dict,
                 include_token: bool = True) -> dict | list:
    payload = json.dumps({"intent": intent, "data": data}).encode("utf-8")
    request = urllib.request.Request(
        _api_url(config), data=payload, headers=_headers(config, include_token), method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=API_TIMEOUT_SECONDS) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            details = json.loads(exc.read().decode("utf-8"))
            message = details.get("message") or details.get("msgDetails")
        except (ValueError, AttributeError):
            message = None
        raise AqaraError(f"Aqara HTTP {exc.code}: {message or 'request rejected'}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise AqaraError(f"Aqara request failed: {getattr(exc, 'reason', str(exc))}") from exc

    if not isinstance(result, dict):
        raise AqaraError("Aqara returned an invalid response")
    code = result.get("code")
    if str(code) != "0":
        message = result.get("message") or result.get("msgDetails") or "unknown API error"
        request_id = result.get("requestId")
        suffix = f" (request {request_id})" if request_id else ""
        raise AqaraError(f"Aqara API {code}: {message}{suffix}")
    if result.get("result") is None:
        return {}
    return result["result"]


def authorize_account() -> None:
    """Authorize the saved Aqara account using Aqara's interface-auth API flow."""
    config = _load_aqara_config()
    if not all(config.get(key) for key in ("app_id", "app_key", "key_id", "account")):
        raise AqaraError("Save the Aqara app credentials and account before authorizing")

    auth_result = _post_intent(
        config,
        "config.auth.getAuthCode",
        {"account": config["account"], "accountType": 0, "accessTokenValidity": "30d"},
        include_token=False,
    )
    if not isinstance(auth_result, dict):
        raise AqaraError("Aqara returned an invalid authorization response")
    auth_code = auth_result.get("authCode")
    if not auth_code:
        raise AqaraError("Aqara did not return an authorization code")

    token_result = _post_intent(
        config,
        "config.auth.getToken",
        {"authCode": auth_code, "account": config["account"], "accountType": 0},
        include_token=False,
    )
    if not isinstance(token_result, dict):
        raise AqaraError("Aqara returned an invalid token response")
    _store_tokens(token_result)


def _store_tokens(token_data: dict) -> None:
    access_token = token_data.get("accessToken") or token_data.get("access_token")
    refresh_token = token_data.get("refreshToken") or token_data.get("refresh_token")
    if not access_token or not refresh_token:
        raise AqaraError("Aqara authorization response did not include both tokens")
    try:
        expires_in = int(token_data.get("expiresIn") or token_data.get("expires_in") or 0)
    except (TypeError, ValueError) as exc:
        raise AqaraError("Aqara returned an invalid token expiration") from exc
    if expires_in <= 0:
        raise AqaraError("Aqara returned a token with no valid expiration")
    _save_aqara_config({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_expires_at": int(time.time()) + expires_in,
        "open_id": token_data.get("openId", ""),
    })


def _ensure_access_token(config: dict) -> dict:
    expires_at = int(config.get("token_expires_at") or 0)
    if expires_at and expires_at <= time.time() + TOKEN_REFRESH_MARGIN_SECONDS:
        refresh_token = config.get("refresh_token")
        if not refresh_token:
            raise AqaraError("Aqara access token expired; authorize the account again")
        result = _post_intent(
            config, "config.auth.refreshToken", {"refreshToken": refresh_token},
            include_token=False,
        )
        _store_tokens(result)
        config = _load_aqara_config()
    return config


def get_devices(config: dict | None = None) -> list[dict]:
    """Fetch account devices and each gateway's child devices."""
    config = _ensure_access_token(config or _load_aqara_config())
    devices_by_id = {}
    page_num = 1
    while True:
        result = _post_intent(
            config, "query.device.info", {"pageNum": page_num, "pageSize": 50}
        )
        page = result.get("data", []) if isinstance(result, dict) else []
        if not isinstance(page, list):
            raise AqaraError("Aqara returned an invalid device list")
        for device in page:
            if device.get("did"):
                devices_by_id[device["did"]] = device
        total_count = int(result.get("totalCount") or len(devices_by_id))
        if not page or len(devices_by_id) >= total_count:
            break
        page_num += 1

    gateways = [
        device for device in devices_by_id.values()
        if str(device.get("modelType")) in ("1", "2") and device.get("did")
    ]
    for gateway in gateways:
        children = _post_intent(
            config, "query.device.subInfo", {"did": gateway["did"]}
        )
        if not isinstance(children, list):
            continue
        for child in children:
            did = child.get("did")
            if did:
                existing = devices_by_id.get(did, {})
                devices_by_id[did] = {
                    **child, **existing,
                    "parentDid": existing.get("parentDid") or child.get("parentDid") or gateway["did"],
                }

    unnamed_children = [
        device["did"] for device in devices_by_id.values()
        if device.get("parentDid") and not device.get("deviceName")
    ]
    for offset in range(0, len(unnamed_children), 50):
        dids = unnamed_children[offset:offset + 50]
        result = _post_intent(
            config, "query.device.info", {"dids": dids, "pageNum": 1, "pageSize": 50}
        )
        details = result.get("data", []) if isinstance(result, dict) else []
        for detail in details:
            if detail.get("did") in devices_by_id:
                devices_by_id[detail["did"]].update(detail)
    return list(devices_by_id.values())


def _resource_kind(resource: dict) -> str | None:
    label = f"{resource.get('name', '')} {resource.get('description', '')}".lower()
    if "humidity" in label:
        return "humidity"
    if "temperature" in label or re.search(r"\btemp\b", label):
        return "temperature"
    if "battery" in label:
        return "battery"
    return None


def _reading_value(value: object, kind: str, resource_id: str) -> float | int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not (-100000 <= number <= 100000):
        return None
    if kind in ("temperature", "humidity"):
        if resource_id in ("0.1.85", "0.2.85") or (
            (kind == "temperature" and not -50 <= number <= 80) or
            (kind == "humidity" and not 0 <= number <= 100)
        ):
            number /= 100
        if (kind == "temperature" and not -50 <= number <= 80) or (
            kind == "humidity" and not 0 <= number <= 100
        ):
            return None
        return round(number, 1)
    if kind == "battery":
        return round(number) if 0 <= number <= 100 else None
    return None


def get_sensors(config: dict | None = None) -> list[dict]:
    """Read supported temperature, humidity, and battery resources for Aqara sensors."""
    config = _ensure_access_token(config or _load_aqara_config())
    if not config.get("access_token"):
        return []

    devices = get_devices(config)
    subdevices = [
        device for device in devices
        if device.get("did") and
        (str(device.get("modelType")) == "3" or device.get("parentDid"))
    ]
    resource_kinds: dict[str, dict[str, str]] = {}
    region = str(config.get("region", "US")).upper()
    models = sorted({device.get("model", "") for device in subdevices if device.get("model")})
    for model in models:
        cache_key = (region, model)
        if cache_key not in _RESOURCE_INFO_CACHE:
            details = _post_intent(config, "query.resource.info", {"model": model})
            discovered = {}
            if isinstance(details, list):
                for item in details:
                    if item.get("resourceId") and (kind := _resource_kind(item)):
                        discovered[str(item["resourceId"])] = kind
            _RESOURCE_INFO_CACHE[cache_key] = discovered
        resource_kinds[model] = _RESOURCE_INFO_CACHE[cache_key]

    readings_request = []
    for device in subdevices:
        resource_ids = list(resource_kinds.get(device.get("model", ""), {}))
        if resource_ids:
            readings_request.append({"subjectId": device["did"], "resourceIds": resource_ids})
    if not readings_request:
        return []

    result = _post_intent(config, "query.resource.value", {"resources": readings_request})
    if not isinstance(result, list):
        raise AqaraError("Aqara returned an invalid sensor resource list")
    values = {
        (item.get("subjectId"), str(item.get("resourceId"))): item.get("value")
        for item in result if item.get("subjectId") and item.get("resourceId")
    }

    sensors = []
    for device in subdevices:
        kinds = resource_kinds.get(device.get("model", ""), {})
        sensor = {
            "did": device["did"],
            "name": device.get("deviceName") or device["did"],
            "model": device.get("model", ""),
            "temperature": None,
            "humidity": None,
            "battery": None,
            "online": str(device.get("state", 0)) == "1",
        }
        for resource_id, kind in kinds.items():
            value = values.get((device["did"], resource_id))
            sensor[kind] = _reading_value(value, kind, resource_id)
        if sensor["temperature"] is not None or sensor["humidity"] is not None:
            sensors.append(sensor)
    return sensors


def disconnect_account() -> None:
    """Remove the current user tokens while retaining app credentials for reconnect."""
    _save_aqara_config({"access_token": "", "refresh_token": "", "token_expires_at": 0,
                        "open_id": ""})
