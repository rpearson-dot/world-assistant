"""When to charge the car: the decision World Assistant makes, in shadow beside Home Assistant's plan until it is
trusted. Pure: given what is true now, the plan (charge, wait, full, unplugged, off) and why.

In order:
1. The master switch Off: off. On: charge whenever plugged in.
2. Not plugged in (the charger Available, Unavailable, or not heard): unplugged.
3. At or above the target: full.
4. A Free Electricity hour you joined; or below the "low" level with "always charge when low" on; or the cheap rate:
   charge.
5. Solar to spare: the house battery full (98% or more) and the house exporting at least `solar_start_w` (or, already
   charging from it, still exporting plus what the car takes at least `solar_stop_w`) for two checks running (10
   minutes, so a passing cloud does not toggle the charger): charge. The charger has no current setting (about 4.6
   kW, start or stop), so it starts only when the spare covers most of that.
6. Otherwise: wait. Never from the house battery (decided 14 November).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

UNPLUGGED = frozenset({"Available", "Unavailable", "unknown", "unavailable", "", None})


@dataclass
class Now:
    mode: str = "auto"
    status: str | None = None
    soc: float | None = None
    target: float = 80.0
    low_on: bool = False
    low: float = 20.0
    cheap: bool = False
    cheap_pence: float | None = None
    free: bool = False
    battery_soc: float | None = None
    export_w: float | None = None
    car_w: float = 0.0
    solar_before: bool = False  # the last check said "solar to spare" too
    settings: dict[str, Any] = field(default_factory=dict)


def solar_spare(n: Now) -> bool:
    if n.battery_soc is None or n.export_w is None or n.battery_soc < 98:
        return False
    start, stop = float(n.settings.get("solar_start_w", 3500)), float(n.settings.get("solar_stop_w", 2500))
    if n.car_w > 500:  # already charging: what would be exported without the car
        return n.export_w + n.car_w >= stop
    return n.export_w >= start


def decide(n: Now) -> tuple[str, str]:
    if n.mode == "off":
        return "off", "the charger is switched Off"
    if n.status in UNPLUGGED or n.status is None:
        return "unplugged", "the car is not plugged in" if n.status else "the charger's state is not known"
    if n.mode == "on":
        return "charge", "the charger is set to On"
    if n.soc is not None and n.soc >= n.target:
        return "full", f"the car is at {n.soc:.0f}%, the target is {n.target:.0f}%"
    if n.free:
        return "charge", "a Free Electricity hour you joined"
    if n.low_on and n.soc is not None and n.soc < n.low:
        return "charge", f"the car is below {n.low:.0f}%"
    if n.cheap:
        return "charge", "the rate is cheap now" + (f" ({n.cheap_pence:g} p/kWh)" if n.cheap_pence is not None else "")
    spare = solar_spare(n)
    if spare and (n.solar_before or n.car_w > 500):
        return "charge", f"solar to spare: the house battery is full and {n.export_w:.0f} W is going to the grid"
    if spare:
        return "wait", "solar to spare: charging if it lasts another 5 minutes"
    return "wait", "waiting for the cheap rate, a free hour or solar to spare"


def same(wa: str, ha: str | None) -> bool | None:
    """Whether World Assistant and Home Assistant agree. Off has no Home Assistant counterpart."""
    if ha in (None, "unknown", "unavailable") or wa == "off":
        return None
    return wa == ha
