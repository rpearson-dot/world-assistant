"""Discovery: what a device probably is (and why), the module that would suit it, private addresses, the register."""

from __future__ import annotations

from app.modules.discovery import identify as ID

OUI = {"A4B1C2": "Fujitsu General Limited", "246F28": "Espressif Inc.", "001E06": "LG Electronics"}
NAMES = {"airstage": "Air conditioning", "ocpp": "EV charger"}
PAGES = {"airstage": "/aircon", "ocpp": "/charger"}


def test_an_air_conditioner_is_named_with_why() -> None:
    dev = {"mac": "a4:b1:c2:00:11:22", "ip": "10.0.2.51", "name": "AirStage-Lounge"}
    f = ID.identify(dev, ["eu.fglair.com"], [], OUI)
    assert f["kind"] == "airstage" and f["sure"] == "likely" and f["module"] == "airstage"
    assert f["why"][0] == "its network adapter is made by Fujitsu General Limited" and "fglair.com" in f["why"][1]
    off = ID.suggestion(f, dev, set(), {}, NAMES, PAGES)
    assert off == {"say": "turn on Air conditioning for it", "module": "airstage", "link": "/settings#setup", "state": "off"}
    todo = ID.suggestion(f, dev, {"airstage"}, {"airstage": set()}, NAMES, PAGES)
    assert todo["state"] == "set up" and todo["say"].endswith("10.0.2.51") and todo["link"] == "/aircon"
    done = ID.suggestion(f, dev, {"airstage"}, {"airstage": {"10.0.2.51"}}, NAMES, PAGES)
    assert done["state"] == "done"


def test_a_common_chip_alone_is_not_enough() -> None:
    plug = {"mac": "24:6f:28:aa:bb:cc", "ip": "10.0.2.80", "name": None}
    assert ID.identify(plug, [], [], OUI)["kind"] is None  # Espressif is in everything
    f = ID.identify(plug, ["api.solarmanpv.com"], [], OUI)
    assert f["kind"] == "inverter" and f["module"] is None
    assert ID.suggestion(f, plug, set(), {}, NAMES, PAGES)["say"].startswith("read through Home Assistant")


def test_names_and_ports_and_private_addresses() -> None:
    phone = {"mac": "da:11:22:33:44:55", "ip": "10.0.2.90", "name": "Robs-iPhone"}
    f = ID.identify(phone, [], [], OUI)
    assert f["kind"] == "phone" and f["sure"] == "possibly" and f["vendor"].startswith("private address")
    pr = ID.identify({"mac": "00:00:00:00:00:01", "name": "office-printer"}, [], [9100], OUI)
    assert pr["kind"] == "printer" and pr["sure"] == "likely"
    assert ID.identify({"mac": "zz"}, [], [], OUI)["vendor"] is None
    assert ID.identify({"mac": "00:1e:06:01:02:03", "name": "x"}, ["lgtvsdp.com"], [], OUI)["kind"] == "lg_tv"


def test_the_register() -> None:
    csv = ('Registry,Assignment,Organization Name,Organization Address\n'
           'MA-L,A4B1C2,Fujitsu General Limited,"Kawasaki JP"\nMA-L,bad,Nobody,x\n')  # fmt: skip
    assert ID.oui_rows(csv) == [("A4B1C2", "Fujitsu General Limited")]
