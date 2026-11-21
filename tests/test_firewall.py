"""The advisory firewall analyser: private hosts ignored, chatty devices flagged, trackers named, essentials kept."""

from __future__ import annotations

from app.modules.network import firewall as fw


def test_private_and_local_hosts_are_not_suggested() -> None:
    assert fw.is_private("192.168.1.10") and fw.is_private("10.0.2.1") and fw.is_private("127.0.0.1")
    assert fw.is_private("169.254.1.1") and fw.is_private(None)
    assert not fw.is_private("8.8.8.8")


def test_a_quiet_device_gets_no_hint() -> None:
    rows = [{"dest": "8.8.8.8", "port": 53, "proto": "udp", "action": "allow", "n": 40, "hours": 10}]
    out = fw.analyse(rows, [], days=7)
    assert out["summary"]["chatty"] is False
    assert out["rule_hint"] is None
    # DNS (port 53) is essential, so it is never suggested for blocking.
    assert out["suggestions"] == []


def test_a_chatty_device_is_flagged_and_gets_a_rule_and_trackers() -> None:
    rows = [{"dest": f"8.8.{i}.8", "port": 443, "proto": "tcp", "action": "allow", "n": 300, "hours": 22} for i in range(40)]
    rows.append({"dest": "192.168.1.5", "port": 8883, "proto": "tcp", "action": "allow", "n": 5000, "hours": 24})  # local: ignored
    dns = [{"dest": "telemetry.example.com", "n": 900}, {"dest": "api.example.com", "n": 100}]
    out = fw.analyse(rows, dns, days=7)
    assert out["summary"]["chatty"] is True
    assert out["summary"]["distinct_dests"] == 40  # the local host does not count
    assert out["rule_hint"] and "except DNS (53) and NTP (123)" in out["rule_hint"]
    assert "telemetry.example.com" in out["tracker_domains"] and "api.example.com" not in out["tracker_domains"]
    assert out["suggestions"] and out["suggestions"][0]["rule"].startswith("Block 8.8.")
    # port 443 is flagged as "may break updates" rather than suggested outright
    assert any("may stop updates" in w for s in out["suggestions"] for w in s["why"])
