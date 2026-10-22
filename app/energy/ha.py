"""The Home Assistant "Energy" device built from /v1/energy/now and /v1/energy/rates."""

from __future__ import annotations

from typing import Any

from app.ha import Device, Sensor


def energy_device(now: dict[str, Any], slots: list[dict[str, Any]]) -> Device:
    extra = session_sensors(now.get("sessions") or {})
    if not now.get("available"):
        return Device(
            "energy",
            "Energy",
            [Sensor("status", "Rates status", "no rates yet", icon="mdi:flash-off"), *extra],
        )
    cur, nxt = now["now"], now.get("next") or {}
    cheap, peak, block = (
        now.get("next_cheap") or {},
        now.get("next_peak") or {},
        now.get("cheapest_block") or {},
    )
    sensors = [
        Sensor(
            "rate",
            "Electricity rate",
            cur["pence"],
            icon="mdi:currency-gbp",
            unit="p/kWh",
            state_class="measurement",
            attributes={"tariff": now["tariff"], "until": cur["until"]},
        ),
        Sensor("band", "Rate band", cur["band"], icon="mdi:chart-timeline-variant"),
        Sensor(
            "cheap_now",
            "Cheap rate now",
            "on" if now["cheap_now"] else "off",
            icon="mdi:piggy-bank",
        ),
        Sensor(
            "next_rate",
            "Next rate",
            nxt.get("pence"),
            icon="mdi:currency-gbp",
            unit="p/kWh",
            attributes={"band": nxt.get("band")},
        ),
        Sensor("next_band", "Next rate band", nxt.get("band"), icon="mdi:chart-timeline-variant"),
        Sensor(
            "next_change",
            "Next rate change",
            nxt.get("at"),
            icon="mdi:clock-start",
            device_class="timestamp",
        ),
        Sensor(
            "minutes_to_change",
            "Minutes to rate change",
            nxt.get("minutes"),
            icon="mdi:timer-sand",
            unit="min",
            state_class="measurement",
        ),
        Sensor(
            "next_cheap_start",
            "Next cheap window starts",
            cheap.get("start"),
            icon="mdi:battery-charging",
            device_class="timestamp",
            attributes={
                "end": cheap.get("end"),
                "hours": cheap.get("hours"),
                "pence": cheap.get("pence"),
            },
        ),
        Sensor(
            "next_cheap_end",
            "Next cheap window ends",
            cheap.get("end"),
            icon="mdi:battery-charging-outline",
            device_class="timestamp",
        ),
        Sensor(
            "next_peak_start",
            "Next peak window starts",
            peak.get("start"),
            icon="mdi:flash-alert",
            device_class="timestamp",
            attributes={"end": peak.get("end"), "pence": peak.get("pence")},
        ),
        Sensor(
            "cheapest_block_start",
            "Cheapest block starts",
            block.get("start"),
            icon="mdi:clock-check",
            device_class="timestamp",
            attributes={
                "end": block.get("end"),
                "hours": block.get("hours"),
                "pence": block.get("pence"),
            },
        ),
        Sensor(
            "average_24h",
            "Average rate next 24h",
            now.get("average_pence"),
            icon="mdi:chart-line",
            unit="p/kWh",
            state_class="measurement",
        ),
        Sensor(
            "slots",
            "Upcoming rates",
            len(slots),
            icon="mdi:calendar-clock",
            state_class="measurement",
            attributes={"slots": slots[:100]},
        ),
        *extra,
    ]
    return Device("energy", "Energy", sensors)


def _next(prefix: str, name: str, block: dict[str, Any], icon: str) -> list[Sensor]:
    nxt = block.get("next") or {}
    joined = block.get("next_joined") or {}
    return [
        Sensor(
            f"{prefix}_next_start",
            f"Next {name} starts",
            nxt.get("start"),
            icon=icon,
            device_class="timestamp",
            attributes={
                "end": nxt.get("end"),
                "minutes": nxt.get("minutes"),
                "joined": nxt.get("joined"),
                "octopoints_per_kwh": nxt.get("octopoints_per_kwh"),
                "upcoming": block.get("upcoming") or [],
            },
        ),
        Sensor(
            f"{prefix}_next_joined_start",
            f"Next joined {name} starts",
            joined.get("start"),
            icon=icon,
            device_class="timestamp",
            attributes={"end": joined.get("end"), "minutes": joined.get("minutes")},
        ),
    ]


def session_sensors(sessions: dict[str, Any]) -> list[Sensor]:
    """Saving Sessions (Octopus pays for using less). Empty when sessions are not known."""
    if not sessions:
        return []
    saving = sessions.get("saving") or {}
    cur_s = saving.get("current") or {}
    return [
        Sensor(
            "saving_session_now",
            "Saving session now",
            "on" if saving.get("now") else "off",
            icon="mdi:piggy-bank-outline",
            attributes={
                "joined": saving.get("now_joined"),
                "end": cur_s.get("end"),
                "octopoints_per_kwh": cur_s.get("octopoints_per_kwh"),
            },
        ),
        *_next("saving_session", "saving session", saving, "mdi:calendar-star"),
    ]
