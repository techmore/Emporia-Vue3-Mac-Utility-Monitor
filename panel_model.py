"""Shared breaker display calculations; never infer an unconfigured rating."""
import math


def breaker_load(watts: float | None, amps: int | None, poles: int = 1) -> dict:
    power_known = type(watts) in (int, float) and math.isfinite(watts)
    unknown = {
        "zone_cls": "", "load_cls": "", "fill_cls": "fill-normal",
        "load_bar_w": 0, "load_label": "Power unavailable" if not power_known else "Rating not set" if not amps else "",
        "safe_bar_pct": 0, "safe_cls": "", "rating_known": bool(amps and amps > 0),
        "power_known": power_known,
    }
    if not power_known or not amps or amps <= 0 or watts == 0:
        return unknown
    current = abs(watts) / (240 if poles == 2 else 120)
    load_pct = current / amps * 100
    reference_pct = load_pct / 0.8
    if reference_pct >= 100:
        zone, load, fill, state = "sz-danger", "load-danger", "fill-danger", "danger"
    elif reference_pct >= 80:
        zone, load, fill, state = "sz-caution", "load-caution", "fill-caution", "warn"
    elif reference_pct >= 60:
        zone, load, fill, state = "sz-moderate", "load-moderate", "fill-moderate", ""
    else:
        zone, load, fill, state = "", "load-normal", "fill-normal", ""
    return {
        "zone_cls": zone, "load_cls": load, "fill_cls": fill,
        "load_bar_w": min(100, load_pct), "load_label": f"{current:.1f}/{amps}A",
        "safe_bar_pct": min(100, reference_pct), "safe_cls": state, "rating_known": True,
        "power_known": True,
    }
