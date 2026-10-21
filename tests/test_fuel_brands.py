from app.fuel import _out
from app.integrations.fuelfinder.queries import (
    FuelHit,
    brand_options,
    compare_cheapest,
    select_brands,
)


def hit(i: str, brand: str | None, pence: float, km: float, supermarket: bool = False) -> FuelHit:
    return FuelHit(i, i, brand, "KT1", "Kingston", 51.4, -0.3, "E10", pence, 2.0, km, False, supermarket)


HITS = [
    hit("a", "Shell", 130.9, 1.0),
    hit("b", "Asda", 133.9, 3.0, True),
    hit("c", "Tesco", 134.9, 2.0, True),
    hit("d", "BP", 140.9, 0.5),
    hit("e", "asda", 135.9, 4.0, True),
    hit("f", None, 136.9, 6.0),
]


def test_select_by_brand_is_case_insensitive() -> None:
    assert {h.node_id for h in select_brands(HITS, ["ASDA"])} == {"b", "e"}


def test_select_supermarkets_and_brand_together() -> None:
    assert {h.node_id for h in select_brands(HITS, ["bp"], True)} == {"b", "c", "d", "e"}


def test_no_selection_keeps_everything() -> None:
    assert len(select_brands(HITS)) == len(HITS)


def test_brand_options_merge_case_and_count() -> None:
    opts = {o["brand"].lower(): o for o in brand_options(HITS)}
    assert opts["asda"]["stations"] == 2 and opts["asda"]["cheapest_pence"] == 133.9
    assert opts["asda"]["supermarket"] is True
    assert "(unbranded)" in opts


def test_comparison_shows_extra_cost_and_distance() -> None:
    chosen = select_brands(HITS, [], True)
    c = compare_cheapest(HITS, chosen, 40)
    assert c is not None
    assert c["cheapest_overall"]["node_id"] == "a" and c["cheapest_selected"]["node_id"] == "b"
    assert c["extra_pence_per_litre"] == 3.0 and c["extra_cost_pounds"] == 1.2
    assert c["same_station"] is False


def test_comparison_none_when_nothing_matches() -> None:
    assert compare_cheapest(HITS, select_brands(HITS, ["nobody"]), 40) is None


def test_out_without_filter_has_no_comparison_but_lists_brands() -> None:
    out = _out("E10", HITS, limit=2)
    assert out["count"] == 2 and "comparison" not in out and out["brands_available"]


def test_out_with_filter_limits_and_compares() -> None:
    out = _out("E10", HITS, brands="tesco, asda", limit=2, fill_litres=30)
    assert [s["node_id"] for s in out["stations"]] == ["b", "c"]
    assert out["comparison"]["extra_cost_pounds"] == 0.9
    assert out["filter"]["supermarkets"] is False


async def test_assistant_mentions_absolute_cheapest(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app import assistant

    async def near(request, **kw):  # type: ignore[no-untyped-def]
        assert kw["brands"] == "tesco" and kw["supermarkets"] is False
        return _out("E10", HITS, brands=kw["brands"], limit=3, place="Home")

    monkeypatch.setattr(assistant, "fuel_near", near)
    out = await assistant.run_tool(None, "fuel_cheapest", {"place": "home", "brands": "tesco"})  # type: ignore[arg-type]
    assert "absolute cheapest is Shell" in out["text"] and "4.0 pence a litre more" in out["text"]


def test_feed_spellings_are_one_brand() -> None:
    from app.integrations.fuelfinder.queries import brand_key, pretty_brand

    assert brand_key("SAINSBURYS") == brand_key("Sainsbury's") == brand_key(" sainsburys ")
    assert pretty_brand("SAINSBURYS") == "Sainsbury's"
    assert pretty_brand("TESCO") == "Tesco" and pretty_brand("Texaco") == "Texaco"
    assert pretty_brand("BP") == "BP" and pretty_brand(None) is None
    hits = [hit("a", "SAINSBURYS", 130.0, 1.0), hit("b", "Sainsbury's", 131.0, 2.0)]
    assert len(select_brands(hits, ["sainsburys"])) == 2
    assert len(brand_options(hits)) == 1


def test_known_supermarket_brand_counts_even_when_the_feed_flag_is_false() -> None:
    from app.integrations.fuelfinder.queries import SUPERMARKET_BRANDS, brand_key

    assert brand_key("SAINSBURYS") in SUPERMARKET_BRANDS and brand_key("Shell") not in SUPERMARKET_BRANDS


def test_hit_builder_marks_sainsburys_a_supermarket_and_tidies_the_name() -> None:
    from datetime import UTC, datetime

    from app.integrations.fuelfinder.queries import _hit

    row = {"node_id": "n", "trading_name": "Sainsbury's Epsom", "brand_name": "SAINSBURYS",
           "postcode": "KT17", "city": "Epsom", "lat": 51.3, "lon": -0.27, "is_motorway": False,
           "is_supermarket": False, "pence": 131.9, "last_updated": None, "effective": None}  # fmt: skip
    h = _hit(row, "E10", 1.2, datetime.now(UTC))
    assert h.brand == "Sainsbury's" and h.supermarket is True


def test_cheapest_overall_is_always_present_whatever_the_filter() -> None:
    plain = _out("E10", HITS, limit=3)
    filtered = _out("E10", HITS, brands="bp", limit=3)
    nothing = _out("E10", HITS, brands="nobody", limit=3)
    for out in (plain, filtered, nothing):
        assert out["cheapest_overall"]["node_id"] == "a"  # Shell, 130.9, whatever is ticked
    assert [s["node_id"] for s in filtered["stations"]] == ["d"]
    assert nothing["stations"] == [] and _out("E10", [], limit=3)["cheapest_overall"] is None
