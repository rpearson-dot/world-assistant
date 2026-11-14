"""Pi-hole v6's API on the home network: reading its query log, and (only when you press Add to Pi-hole) blocking
domains for one device.

Signing in: POST /api/auth {"password": <app password>} gives a session id, sent as X-FTL-SID; the session is kept
while it is valid and given back afterwards. The app password is made in Pi-hole (Settings → Web interface / API →
Configure app password) and saved on World Assistant's Keys page.

Your groups (Kids, Adults, IoT, ...) are read and shown, never changed. A block goes either to one of your groups
(every device in it) or to one device: then the device also joins a small group of its own (wa-<name>), beside the
groups it is already in, so your lists keep applying to it. Undo takes out only what World Assistant added.
"""

from __future__ import annotations

import re
from typing import Any, Self
from urllib.parse import quote

import httpx

from app.modules.network import parse

GROUP = "wa-"


class PiholeError(Exception):
    pass


class Pihole:
    def __init__(self, base: str, password: str, client: httpx.AsyncClient | None = None) -> None:
        self.base, self.password = base.rstrip("/"), password
        self.client = client or httpx.AsyncClient(timeout=30)
        self.sid: str | None = None

    async def __aenter__(self) -> Self:
        r = await self.client.post(f"{self.base}/api/auth", json={"password": self.password})
        if r.status_code in (401, 403):
            raise PiholeError("Pi-hole refused the app password (Settings → Web interface / API → Configure app password)")
        r.raise_for_status()
        sess = (r.json() or {}).get("session") or {}
        if not sess.get("valid") and not sess.get("sid"):
            raise PiholeError("Pi-hole did not open a session: " + str((r.json() or {}).get("session", {}).get("message") or "no reason given"))
        self.sid = sess.get("sid")
        return self

    async def __aexit__(self, *exc: object) -> None:
        try:
            if self.sid:
                await self.client.delete(f"{self.base}/api/auth", headers=self._h())
        except httpx.HTTPError:
            pass

    def _h(self) -> dict[str, str]:
        return {"X-FTL-SID": self.sid} if self.sid else {}

    async def _call(self, method: str, path: str, **kw: Any) -> Any:
        r = await self.client.request(method, f"{self.base}{path}", headers=self._h(), **kw)
        if r.status_code == 404 and method == "DELETE":
            return None
        if r.status_code >= 400:
            try:
                why = ((r.json() or {}).get("error") or {}).get("message")
            except ValueError:
                why = None
            raise PiholeError(f"Pi-hole {method} {path}: HTTP {r.status_code}" + (f" ({why})" if why else ""))
        return r.json() if r.content else None

    async def queries(self, since: float, until: float, after_id: int = 0) -> list[dict[str, Any]]:
        """Queries between two times (seconds since 1970), oldest first, without those up to after_id."""
        out: list[dict[str, Any]] = []
        start = 0
        for _ in range(20):
            body = await self._call("GET", "/api/queries", params={"from": int(since), "until": int(until) + 1,
                                                                   "length": 5000, "start": start})  # fmt: skip
            got = (body or {}).get("queries") or []
            out += [q for q in got if int(q.get("id") or 0) > after_id]
            if len(got) < 5000:
                break
            start += len(got)
        return sorted(out, key=lambda q: (q.get("time") or 0, q.get("id") or 0))

    # --- your groups, and blocking for one device or one group -------------------------------------------------

    async def groups(self) -> list[dict[str, Any]]:
        body = await self._call("GET", "/api/groups")
        return [{"id": int(g["id"]), "name": g.get("name"), "enabled": g.get("enabled", True)} for g in (body or {}).get("groups") or []]

    async def clients(self) -> list[dict[str, Any]]:
        """Your client entries (by IP, MAC, host name or subnet) and the groups each is in."""
        body = await self._call("GET", "/api/clients")
        return [{"client": str(c.get("client")), "groups": [int(x) for x in c.get("groups") or []], "name": c.get("name"),
                 "comment": c.get("comment")} for c in (body or {}).get("clients") or []]  # fmt: skip

    async def group_id(self, name: str, create: bool) -> int | None:
        for g in await self.groups():
            if g["name"] == name:
                return g["id"]
        if not create:
            return None
        await self._call("POST", "/api/groups", json={"name": name, "comment": "World Assistant: blocks for one device", "enabled": True})
        return await self.group_id(name, False)

    async def block(self, device: dict[str, Any], domains: list[str], group_id: int | None = None) -> dict[str, Any]:
        """The domains blocked for one device (its own extra group, wa-<name>, beside the groups it is already in) or
        for one of your groups (group_id). Nothing is moved out of a group."""
        label = device.get("label") or device.get("ip")
        if group_id is None:
            name = own_group(label)
            gid = await self.group_id(name, True)
            if gid is None:
                raise PiholeError("could not make the device's group")
            entry = match(await self.clients(), device)
            if entry is None:  # no entry for it yet: one by its address, in the Default group and its own
                await self._call("POST", "/api/clients", json={"client": device["ip"], "comment": f"World Assistant: {label}", "groups": [0, gid]})
                client = device["ip"]
            else:
                client = entry["client"]
                if gid not in entry["groups"]:
                    await self._call("PUT", f"/api/clients/{quote(client, safe='')}",
                                     json={"comment": entry.get("comment") or "", "groups": sorted({*entry["groups"], gid})})  # fmt: skip
            target = {"group": name, "group_id": gid, "client": client}
        else:
            target = {"group_id": group_id}
        for d in domains:
            await self._call("POST", "/api/domains/deny/exact", json={"domain": d, "comment": f"World Assistant: {label}",
                                                                      "groups": [target["group_id"]], "enabled": True})  # fmt: skip
        return {**target, "domains": domains}

    async def unblock(self, domains: list[str], device: dict[str, Any] | None = None, all_gone: bool = False) -> None:
        """The domains taken back out. With a device and all_gone: its own group is left and removed (the groups you
        made are never touched)."""
        for d in domains:
            await self._call("DELETE", f"/api/domains/deny/exact/{quote(d, safe='')}")
        if device and all_gone:
            name = own_group(device.get("label") or device.get("ip"))
            gid = await self.group_id(name, False)
            entry = match(await self.clients(), device)
            if entry is not None and gid is not None and gid in entry["groups"]:
                rest = [g for g in entry["groups"] if g != gid]
                if (entry.get("comment") or "").startswith("World Assistant:") and rest == [0]:
                    await self._call("DELETE", f"/api/clients/{quote(entry['client'], safe='')}")
                else:
                    await self._call("PUT", f"/api/clients/{quote(entry['client'], safe='')}", json={"comment": entry.get("comment") or "", "groups": rest})
            if gid is not None:
                await self._call("DELETE", f"/api/groups/{quote(name, safe='')}")


def own_group(label: str) -> str:
    return GROUP + (re.sub(r"[^a-z0-9-]+", "-", str(label).lower()).strip("-")[:40] or "device")


def match(entries: list[dict[str, Any]], device: dict[str, Any]) -> dict[str, Any] | None:
    """Your client entry for a device: by its MAC, its IP or its name (as Pi-hole knows it)."""
    keys = {str(device.get(k) or "").lower() for k in ("mac", "ip", "name") if device.get(k)}
    for e in entries:
        if e["client"].lower() in keys:
            return e
    return None


def groups_of(entries: list[dict[str, Any]], groups: list[dict[str, Any]], device: dict[str, Any]) -> list[str]:
    """The names of your groups a device is in (Default left out, and World Assistant's own)."""
    e = match(entries, device)
    names = {g["id"]: g["name"] for g in groups}
    return [names.get(i, str(i)) for i in (e["groups"] if e else [0]) if i != 0 and not str(names.get(i, "")).startswith(GROUP)]


def events(qs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [parse.pihole(q) for q in qs if q.get("domain")]
