"""OCPP 1.6J, the part a home charger needs, as pure functions: reading and writing frames, the central system's
answer to each thing a charger says, and reading meter values. No network or database here, so it is tested on its
own; server.py does the talking and keeping.

A frame is a JSON array: [2, id, action, payload] a call, [3, id, payload] its result, [4, id, code, text, details]
an error.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

CALL, RESULT, ERROR = 2, 3, 4
HEARTBEAT_S = 300
ID_TAG = "WorldAssistant"  # what World Assistant starts a charge with (the charger has no cards in use)
# What the central system can ask the charger to do (1.6 core and the trigger, availability and unlock extras)
COMMANDS = ("RemoteStartTransaction", "RemoteStopTransaction", "Reset", "GetConfiguration", "ChangeConfiguration",
            "TriggerMessage", "UnlockConnector", "ChangeAvailability", "ClearCache")  # fmt: skip


class FrameError(ValueError):
    """A frame that is not OCPP-J: answered with a CALLERROR when it has an id, otherwise only recorded."""

    def __init__(self, message: str, unique_id: str | None = None, code: str = "FormationViolation") -> None:
        super().__init__(message)
        self.unique_id, self.code = unique_id, code


@dataclass
class Frame:
    kind: int
    unique_id: str
    action: str | None = None
    payload: Any = None
    error_code: str | None = None
    error_text: str | None = None
    raw: str = ""


def parse(raw: str) -> Frame:
    if len(raw) > 256 * 1024:
        raise FrameError("frame larger than 256 KB")
    try:
        msg = json.loads(raw)
    except ValueError as exc:
        raise FrameError(f"not JSON ({exc})") from exc
    if not isinstance(msg, list) or len(msg) < 3 or msg[0] not in (CALL, RESULT, ERROR) or not isinstance(msg[1], str):
        raise FrameError("not an OCPP-J frame", msg[1] if isinstance(msg, list) and len(msg) > 1 and isinstance(msg[1], str) else None)
    kind, uid = msg[0], msg[1]
    if kind == CALL:
        if len(msg) != 4 or not isinstance(msg[2], str) or not isinstance(msg[3], dict):
            raise FrameError("a call is [2, id, action, {payload}]", uid)
        return Frame(CALL, uid, msg[2], msg[3], raw=raw)
    if kind == RESULT:
        return Frame(RESULT, uid, payload=msg[2], raw=raw)
    return Frame(ERROR, uid, error_code=str(msg[2]), error_text=str(msg[3]) if len(msg) > 3 else "",
                 payload=msg[4] if len(msg) > 4 else {}, raw=raw)  # fmt: skip


def call(action: str, payload: dict[str, Any]) -> tuple[str, str]:
    uid = uuid.uuid4().hex[:20]
    return uid, json.dumps([CALL, uid, action, payload], separators=(",", ":"))


def result(uid: str, payload: dict[str, Any]) -> str:
    return json.dumps([RESULT, uid, payload], separators=(",", ":"))


def error(uid: str, code: str, text: str) -> str:
    return json.dumps([ERROR, uid, code, text[:200], {}], separators=(",", ":"))


def now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).isoformat().replace("+00:00", "Z")


# --- meter values --------------------------------------------------------------------------------------------------

_SCALE = {"Wh": 1.0, "kWh": 1000.0, "W": 1.0, "kW": 1000.0, "A": 1.0, "V": 1.0, "Percent": 1.0, "Celsius": 1.0}
_NAMES = {"Energy.Active.Import.Register": "energy_wh", "Power.Active.Import": "power_w", "Current.Import": "current_a",
          "Current.Offered": "offered_a", "Voltage": "voltage_v", "SoC": "soc", "Temperature": "temperature_c"}  # fmt: skip


def meter(values: list[dict[str, Any]]) -> dict[str, Any]:
    """The newest reading of each kind from a MeterValues (or StopTransaction) list: energy in Wh, power in W, and
    current, voltage, state of charge, temperature. Per-phase values of the same kind are added (power, current) or
    the first kept (voltage)."""
    out: dict[str, Any] = {}
    newest = sorted(values or [], key=lambda v: str(v.get("timestamp") or ""))
    for mv in newest:
        seen: dict[str, float] = {}
        for sv in mv.get("sampledValue") or []:
            name = _NAMES.get(sv.get("measurand") or "Energy.Active.Import.Register")
            if not name:
                continue
            try:
                v = float(sv.get("value")) * _SCALE.get(sv.get("unit") or ("Wh" if name == "energy_wh" else ""), 1.0)
            except (TypeError, ValueError):
                continue
            if sv.get("phase") and name in ("power_w", "current_a") and not str(sv.get("phase")).endswith("-N"):
                seen[name] = seen.get(name, 0.0) + v
            elif sv.get("phase") and name in seen:
                continue
            else:
                seen[name] = v
        if seen:
            out.update(seen)
            out["at"] = mv.get("timestamp")
    return out


# --- what the central system answers ------------------------------------------------------------------------------


@dataclass
class Effects:
    """What a call from the charger means: the answer to send, and what to record."""

    answer: dict[str, Any] | None = None
    error: tuple[str, str] | None = None
    events: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)  # (kind, text, detail)
    charger: dict[str, Any] = field(default_factory=dict)  # columns of ocpp_charger to set
    session_start: dict[str, Any] | None = None
    session_stop: dict[str, Any] | None = None
    session_meter: dict[str, Any] | None = None


def respond(action: str, p: dict[str, Any], *, connected_s: float, booted_before: bool, transaction_id: int,
            now: datetime | None = None, last_status: str | None = None) -> Effects:
    """The answer to one call from the charger.

    connected_s: how long this connection has been open; booted_before: whether this connection has already seen a
    BootNotification (a second one means the charger restarted without dropping the link); transaction_id: the id
    to give a StartTransaction."""
    t = now or datetime.now(UTC)
    e = Effects()
    if action == "BootNotification":
        e.answer = {"status": "Accepted", "currentTime": now_iso(t), "interval": HEARTBEAT_S}
        what = " ".join(str(p.get(k)) for k in ("chargePointVendor", "chargePointModel") if p.get(k))
        fw = p.get("firmwareVersion")
        why = ("restarted while still connected" if booted_before else
               "started up" if connected_s < 120 else "said it started up, after being connected a while")  # fmt: skip
        about = ", ".join(x for x in (what, f"firmware {fw}" if fw else "") if x)
        e.events.append(("boot", f"Charger {why}" + (f" ({about})" if about else ""), p))
        e.charger = {"boot": p, "booted_at": t, "heartbeat_s": HEARTBEAT_S}
    elif action == "Heartbeat":
        e.answer = {"currentTime": now_iso(t)}
    elif action == "StatusNotification":
        e.answer = {}
        conn, status, code = p.get("connectorId", 0), p.get("status"), p.get("errorCode") or "NoError"
        e.charger = {"status_info": p}
        if conn != 0 or not last_status:
            e.charger.update(status=status, error_code=code)
        if code != "NoError":
            extra = ", ".join(str(p[k]) for k in ("info", "vendorErrorCode", "vendorId") if p.get(k))
            e.events.append(("fault", f"Fault: {code}" + (f" ({extra})" if extra else "") + f", status {status}", p))
        if status != last_status:
            e.events.append(("status", f"{'Charger' if conn == 0 else 'Connector ' + str(conn)}: {status}", p))
    elif action == "Authorize":
        e.answer = {"idTagInfo": {"status": "Accepted"}}
    elif action == "StartTransaction":
        e.answer = {"transactionId": transaction_id, "idTagInfo": {"status": "Accepted"}}
        e.session_start = {"transaction_id": transaction_id, "connector": p.get("connectorId"), "id_tag": p.get("idTag"),
                           "started_at": p.get("timestamp") or now_iso(t), "meter_start_wh": p.get("meterStart")}  # fmt: skip
        e.events.append(("session", f"Charging session {transaction_id} started (meter {p.get('meterStart')} Wh)", p))
    elif action == "StopTransaction":
        e.answer = {"idTagInfo": {"status": "Accepted"}}
        m = meter(p.get("transactionData") or [])
        e.session_stop = {"transaction_id": p.get("transactionId"), "stopped_at": p.get("timestamp") or now_iso(t),
                          "meter_stop_wh": p.get("meterStop"), "reason": p.get("reason") or "Local"}  # fmt: skip
        e.events.append(("session", (f"Charging session {p.get('transactionId')} stopped ({p.get('reason') or 'Local'}, "
                                    f"meter {p.get('meterStop')} Wh)"), {**p, "meter": m}))  # fmt: skip
    elif action == "MeterValues":
        e.answer = {}
        m = meter(p.get("meterValue") or [])
        if m:
            e.charger = {"meter": m}
            if p.get("transactionId") is not None:
                e.session_meter = {"transaction_id": p.get("transactionId"), **m}
    elif action == "DataTransfer":
        e.answer = {"status": "UnknownVendorId"}
        e.events.append(("data", f"Vendor data from {p.get('vendorId')}: {p.get('messageId') or ''}".strip(), p))
    elif action in ("DiagnosticsStatusNotification", "FirmwareStatusNotification"):
        e.answer = {}
        e.events.append(("firmware", f"{action.replace('Notification', '')}: {p.get('status')}", p))
    else:
        e.error = ("NotImplemented", f"{action} is not handled by World Assistant")
    return e


def command_payload(action: str, args: dict[str, Any], *, transaction_id: int | None) -> dict[str, Any]:
    """The payload for a command, from the few things a person gives. Raises ValueError when it cannot be sent."""
    if action not in COMMANDS:
        raise ValueError(f"not a command World Assistant sends: {action}")
    if action == "RemoteStartTransaction":
        return {"connectorId": int(args.get("connector") or 1), "idTag": str(args.get("id_tag") or ID_TAG)[:20]}
    if action == "RemoteStopTransaction":
        tid = args.get("transaction_id", transaction_id)
        if tid is None:
            raise ValueError("no charging session is known to stop (the charger has not said one started)")
        return {"transactionId": int(tid)}
    if action == "Reset":
        kind = str(args.get("type") or "Soft").capitalize()
        if kind not in ("Soft", "Hard"):
            raise ValueError("a reset is Soft or Hard")
        return {"type": kind}
    if action == "GetConfiguration":
        return {"key": list(args["keys"])} if args.get("keys") else {}
    if action == "ChangeConfiguration":
        if not args.get("key"):
            raise ValueError("which setting (key) to change")
        return {"key": str(args["key"])[:50], "value": str(args.get("value", ""))[:500]}
    if action == "TriggerMessage":
        msg = str(args.get("message") or "StatusNotification")
        if msg not in ("BootNotification", "DiagnosticsStatusNotification", "FirmwareStatusNotification", "Heartbeat",
                       "MeterValues", "StatusNotification"):  # fmt: skip
            raise ValueError(f"the charger cannot be asked for {msg}")
        return {"requestedMessage": msg, **({"connectorId": int(args["connector"])} if args.get("connector") else {})}
    if action == "UnlockConnector":
        return {"connectorId": int(args.get("connector") or 1)}
    if action == "ChangeAvailability":
        kind = str(args.get("type") or "Operative").capitalize()
        if kind not in ("Operative", "Inoperative"):
            raise ValueError("availability is Operative or Inoperative")
        return {"connectorId": int(args.get("connector") or 0), "type": kind}
    return {}
