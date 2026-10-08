"""Explicit same-window solar scenarios, not system sizing or tariff forecasts."""
import math


def _number(value, name: str, maximum: float = 1_000_000) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a measured or explicitly entered number')
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError(f'{name} exceeds the supported range') from exc
    if not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError(f'{name} must be finite and between 0 and {maximum:g}')
    return value


def solar_offset(consumption_kwh: float, generation_kwh: float,
                 self_consumption_pct: float, usage_rate_cents: float,
                 export_rate_cents: float | None = None) -> dict:
    """Calculate one shared window; fixed charges, financing and payback are excluded.

    The requested self-consumption fraction is a user assumption, not an hourly
    matching simulation. Unused generation is exported; unknown credits remain
    unknown rather than implicitly zero or retail-rate net metering.
    """
    consumption = _number(consumption_kwh, 'Consumption')
    generation = _number(generation_kwh, 'Generation')
    fraction = _number(self_consumption_pct, 'Self-consumption percent', 100) / 100
    usage_rate = _number(usage_rate_cents, 'Usage rate') / 100
    export_rate = (None if export_rate_cents is None else
                   _number(export_rate_cents, 'Export rate') / 100)
    self_consumed = min(consumption, generation * fraction)
    exported = generation - self_consumed
    avoided = self_consumed * usage_rate
    credit = None if exported > 0 and export_rate is None else exported * (export_rate or 0)
    return {
        'consumption_kwh': consumption, 'generation_kwh': generation,
        'self_consumed_kwh': self_consumed, 'exported_kwh': exported,
        'remaining_grid_kwh': consumption - self_consumed,
        'avoided_usage_cost': avoided, 'export_credit': credit,
        'total_value': None if credit is None else avoided + credit,
        'offset_pct': None if consumption == 0 else self_consumed / consumption * 100,
    }
