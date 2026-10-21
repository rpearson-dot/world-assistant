"""Build a tracker config from a few plain choices, so nobody has to write stop ids or JSON.

Given a line's stop order in both directions, the stop you board at and the stop you need to
reach, this works out the direction, the opposite-side stop at the target (for buses that
terminate there), the stop before the target, and the legs to time.
"""

from __future__ import annotations

import re
from itertools import pairwise
from typing import Any

Seqs = dict[str, list[dict[str, str]]]  # direction -> [{id, name}]


class SetupError(ValueError):
    pass


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def slug(name: str, limit: int = 18) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:limit].strip("_")
    return s if s and s[0].isalpha() else f"s_{s}"[:limit] or "stop"


def describe(seqs: Seqs) -> dict[str, Any]:
    """Stops by direction, labelled by where that direction ends up."""
    return {
        d: {
            "towards": stops[-1]["name"] if stops else "",
            "stops": [{**s, "pos": i} for i, s in enumerate(stops)],
        }
        for d, stops in seqs.items()
    }


def _find(seqs: Seqs, sid: str) -> tuple[str, int]:
    for d, stops in seqs.items():
        for i, s in enumerate(stops):
            if s["id"] == sid:
                return d, i
    raise SetupError(f"stop {sid} is not on this line")


def _opposite_same_name(seqs: Seqs, direction: str, name: str) -> dict[str, str] | None:
    for d, stops in seqs.items():
        if d != direction:
            for s in stops:
                if norm(s["name"]) == norm(name):
                    return s
    return None


def build_config(
    line: str,
    seqs: Seqs,
    board_sid: str,
    target_sid: str,
    watch_sids: list[str] | None = None,
    earliest: str = "07:00",
    deadline: str = "08:10",
    min_travel_min: int = 12,
    poll_start: str = "06:30",
    poll_end: str = "09:30",
) -> dict[str, Any]:
    b_dir, b_pos = _find(seqs, board_sid)
    t_dir, t_pos = _find(seqs, target_sid)
    if b_dir != t_dir:
        raise SetupError(
            "the two stops are on opposite sides of the road; pick the stop you get off at in the same direction"
        )
    if t_pos <= b_pos:
        raise SetupError(
            "the stop you need to reach comes before the stop you get on at in that direction"
        )
    route = seqs[b_dir]
    board, target = route[b_pos], route[t_pos]
    used: set[str] = set()
    stops: list[dict[str, str]] = []

    def add(key: str, s: dict[str, str], label: str, place: str | None = None) -> str:
        base, n = key, 2
        while key in used:
            key, n = f"{base}_{n}", n + 1
        used.add(key)
        item = {"key": key, "sid": s["id"], "label": label}
        if place:
            item["place"] = place
        stops.append(item)
        return key

    k_board = add("board", board, board["name"])
    watched: list[tuple[int, str]] = []
    for sid in watch_sids or []:
        d, pos = _find(seqs, sid)
        s = seqs[d][pos]
        if sid in (board_sid, target_sid) or any(x["sid"] == sid for x in stops):
            continue
        place = slug(s["name"])
        other = _opposite_same_name(seqs, d, s["name"])
        side = "towards " + (seqs[d][-1]["name"] if seqs[d] else d)
        key = add(
            slug(s["name"]) + "_" + d[:3], s, f"{s['name']} ({side})", place if other else None
        )
        if other and not any(x["sid"] == other["id"] for x in stops):
            od = next(dd for dd, ss in seqs.items() if other in ss)
            add(
                slug(s["name"]) + "_" + od[:3],
                other,
                f"{other['name']} (towards {seqs[od][-1]['name']})",
                place,
            )
        if d == b_dir and b_pos < pos < t_pos:
            watched.append((pos, key))
    k_before = (
        add("before_target", route[t_pos - 1], route[t_pos - 1]["name"])
        if t_pos - 1 > b_pos
        else None
    )
    k_target = add("target", target, target["name"])
    ret = _opposite_same_name(seqs, b_dir, target["name"])
    k_ret = add("target_return", ret, f"{ret['name']} (return side)") if ret else None
    chain = [k_board, *[k for _, k in sorted(watched)], k_target]
    cfg: dict[str, Any] = {
        "line": line,
        "stops": stops,
        "target": {
            "board": k_board,
            "alight": k_target,
            "alight_return": k_ret,
            "before_alight": k_before,
            "direction": b_dir,
        },
        "segments": [[a, b] for a, b in pairwise(chain)],
        "earliest": earliest,
        "deadline": deadline,
        "min_travel_min": min_travel_min,
        "poll_start": poll_start,
        "poll_end": poll_end,
    }
    return cfg
