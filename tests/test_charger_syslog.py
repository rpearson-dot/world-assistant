"""The charger's own syslog: reading one line (severity, facility, host, message), and the listener telling charger
lines apart from UniFi lines — by the address the charger connects from, and, should that have changed, by the
charge-point ID it names itself — while an unmatched line still goes the ordinary way."""

from __future__ import annotations

from app.modules.network.listener import Syslog
from app.modules.ocpp import syslog


def test_a_syslog_line_is_read() -> None:
    a = syslog.read("<11>Nov 21 12:00:01 SYNCEV-U1HKP app: wifi:2 lost association")
    assert a["severity"] == 3 and syslog.severity_word(a["severity"]) == "error"
    assert a["facility"] == 1 and a["message"] == "wifi:2 lost association"

    b = syslog.read("<14>1 2026-11-21T12:00:00Z SYNCEV-U1HKP charger - - - EV plugged in")
    assert b["severity"] == 6 and b["message"] == "EV plugged in"

    c = syslog.read("just text, no header")  # no priority, no recognised header: kept as-is
    assert c["severity"] is None and c["facility"] is None and c["message"] == "just text, no header"

    d = syslog.read("<999>Nov 21 12:00:02 host app: nonsense priority")  # out of range: not treated as a priority
    assert d["severity"] is None and d["facility"] is None


def test_the_host_a_line_names_is_found() -> None:
    assert syslog.hostname("<11>Nov 21 12:00:01 SYNCEV-U1HKP app: x") == "syncev-u1hkp"
    assert syslog.hostname("<14>1 2026-11-21T12:00:00Z SYNCEV-U1HKP charger - - - x") == "syncev-u1hkp"
    assert syslog.hostname("<14>1 2026-11-21T12:00:00Z - app - - - x") is None  # RFC 5424 nil host
    assert syslog.hostname("no header at all") is None


def test_the_listener_keeps_charger_lines_apart() -> None:
    proto = Syslog()
    proto.charger_ips = {"10.0.2.101": "U1HKP"}
    proto.charger_hosts = {"syncev-u1hkp": "U1HKP"}

    # From the charger's address: a charger log line, not a network event
    proto.datagram_received(b"<11>Nov 21 12:00:01 SYNCEV-U1HKP app: fault F12", ("10.0.2.101", 514))
    assert len(proto.charger_queue) == 1 and not proto.queue
    assert proto.charger_queue[0]["charger"] == "U1HKP"
    assert proto.charger_queue[0]["message"] == "fault F12" and proto.charger_queue[0]["from_ip"] == "10.0.2.101"

    # Address changed (new DHCP lease), but it still names itself the charge-point ID: still matched
    proto.datagram_received(b"<14>Nov 21 12:00:02 SYNCEV-U1HKP app: charging", ("10.0.2.202", 514))
    assert len(proto.charger_queue) == 2 and not proto.queue

    # Anything else from the home network goes the ordinary way, so nothing is lost
    proto.datagram_received(b"<30>Nov 21 12:00:03 UDR dnsmasq-dhcp[1]: DHCPACK(br0) 10.0.2.50 aa:bb:cc:dd:ee:ff phone",
                            ("10.0.2.1", 514))
    assert len(proto.charger_queue) == 2 and len(proto.queue) == 1


class _FakeWriter:
    def __init__(self, ip: str) -> None:
        self._ip = ip
        self.closed = False

    def get_extra_info(self, _key: str) -> tuple[str, int]:
        return (self._ip, 55555)

    def close(self) -> None:
        self.closed = True


def _feed_tcp(proto: Syslog, ip: str, data: bytes) -> None:
    import asyncio

    async def go() -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        await proto.handle_tcp(reader, _FakeWriter(ip))  # type: ignore[arg-type]

    asyncio.run(go())


def test_tcp_syslog_is_read_line_by_line_and_routed() -> None:
    # Many chargers send syslog over TCP to a plain "IP + port"; the listener reads newline-delimited lines, and keeps
    # a final line with no trailing newline.
    proto = Syslog()
    proto.charger_ips = {"10.0.2.101": "U1HKP"}
    _feed_tcp(proto, "10.0.2.101", b"<11>Nov 21 12:00:01 SYNCEV: plugged in\n<14>Nov 21 12:00:02 SYNCEV: charging\n<14>Nov 21 12:00:03 SYNCEV: done")
    assert [r["message"] for r in proto.charger_queue] == ["plugged in", "charging", "done"]
    assert not proto.queue

    # A non-charger device over TCP still goes the ordinary way; a line from outside the home network is refused.
    other = Syslog()
    _feed_tcp(other, "10.0.2.1", b"<30>Nov 21 12:00:03 UDR dnsmasq-dhcp[1]: DHCPACK(br0) 10.0.2.50 aa:bb:cc:dd:ee:ff phone\n")
    assert len(other.queue) == 1 and not other.charger_queue
    public = Syslog()
    _feed_tcp(public, "8.8.8.8", b"<14>Nov 21 12:00:04 somewhere: ignored\n")
    assert not public.queue and not public.charger_queue
