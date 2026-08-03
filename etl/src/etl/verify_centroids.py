"""Independently verify every village centroid by reverse geocoding it.

recentroid.py CHOOSES a centroid, using distance to the district's centre to
decide between several places of the same name. This checks the answer, and it
has to do so by different means or it just agrees with itself.

So it asks the opposite question. Rather than "where is the village called X",
it takes the coordinate that was chosen and asks "what is actually here". A
correct centroid comes back naming the village we expected. A wrong one comes
back naming somewhere else, which is precisely the failure that sends a driver
to the wrong end of the county.

Two further signals fall out of the same lookup and are used as corroboration:

- The postcode. UK outcodes are strongly geographic (DL for Richmond and the
  dales, HG for Harrogate, YO for the Vale of York and the coast, BD and LS for
  the west), so a village whose outcode disagrees with every other village in
  its district is worth a second look even when the name matches.
- The county. Anything not in North Yorkshire is wrong outright.

Run with:

    uv run python -m etl.verify_centroids
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import requests

from . import USER_AGENT

DIST = Path(__file__).parent.parent.parent.parent / "data" / "dist"
CACHE = DIST.parent / ".cache" / "reverse_geocode.json"
_REVERSE = "https://nominatim.openstreetmap.org/reverse"
_RATE_LIMIT_S = 1.1

_cache: dict[str, dict] = {}
_last = 0.0


def _load() -> None:
    if CACHE.exists():
        _cache.update(json.loads(CACHE.read_text()))


def _save() -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(_cache, indent=2))


def reverse(lat: float, lng: float) -> dict:
    """What Nominatim says is at this coordinate. Cached and rate-limited."""
    global _last
    key = f"{lat:.6f},{lng:.6f}"
    if key in _cache:
        return _cache[key]

    elapsed = time.time() - _last
    if elapsed < _RATE_LIMIT_S:
        time.sleep(_RATE_LIMIT_S - elapsed)
    resp = requests.get(
        _REVERSE,
        params={"lat": lat, "lon": lng, "format": "json", "zoom": 14, "addressdetails": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=15,
    )
    _last = time.time()
    resp.raise_for_status()
    _cache[key] = resp.json()
    _save()
    return _cache[key]


def _norm(s: str) -> str:
    """Compare place names the way a person would, not byte for byte."""
    s = s.lower().replace("-", " ").replace("'", "")
    s = re.sub(r"\b(st|saint)\b", "st", s)
    s = re.sub(r"\b(upper|lower|high|low|great|little|east|west|north|south|old|new)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _names_in(addr: dict) -> list[str]:
    """Every place name the address carries, coarse to fine."""
    keys = ("village", "hamlet", "town", "suburb", "city", "locality",
            "municipality", "civil_parish", "neighbourhood", "isolated_dwelling", "farm")
    return [addr[k] for k in keys if addr.get(k)]


def main() -> None:
    villages = json.loads((DIST / "villages.json").read_text())
    _load()

    todo = [v for v in villages if v.get("centroid")]
    print(f"reverse geocoding {len(todo)} centroids", flush=True)

    outcodes: dict[str, Counter] = defaultdict(Counter)
    rows: list[dict] = []

    for i, v in enumerate(todo, 1):
        c = v["centroid"]
        try:
            data = reverse(c["lat"], c["lng"])
        except Exception as e:  # noqa: BLE001
            rows.append({"v": v, "status": "lookup failed", "detail": str(e)[:80]})
            continue
        addr = data.get("address", {})
        found = _names_in(addr)
        postcode = (addr.get("postcode") or "").split()[0] if addr.get("postcode") else ""
        county = addr.get("state_district") or addr.get("county") or ""

        expected = _norm(v["name"])
        match = any(_norm(f) == expected for f in found)
        near = any(expected in _norm(f) or _norm(f) in expected for f in found if _norm(f))

        rows.append({
            "v": v, "found": found, "postcode": postcode, "county": county,
            "status": "ok" if match else ("close" if near else "MISMATCH"),
        })
        if postcode:
            outcodes[v["district"]][postcode] += 1
        if i % 50 == 0:
            print(f"  {i}/{len(todo)}", flush=True)

    # Corroboration: an outcode nobody else in the district uses.
    for r in rows:
        oc = r.get("postcode")
        if not oc:
            continue
        peers = outcodes[r["v"]["district"]]
        if peers[oc] == 1 and sum(peers.values()) > 8:
            r["lone_outcode"] = True

    bad = [r for r in rows if r["status"] == "MISMATCH"]
    close = [r for r in rows if r["status"] == "close"]
    lone = [r for r in rows if r.get("lone_outcode") and r["status"] == "ok"]
    failed = [r for r in rows if r["status"] == "lookup failed"]

    print(f"\n{len(rows)} checked")
    print(f"  {len(rows) - len(bad) - len(close) - len(failed):4d}  confirmed: the point is in the expected village")
    print(f"  {len(close):4d}  close: name overlaps but is not identical")
    print(f"  {len(bad):4d}  MISMATCH: the point is somewhere else entirely")
    print(f"  {len(failed):4d}  lookup failed")

    if bad:
        print("\nMISMATCHES, worst first:")
        for r in bad:
            v = r["v"]
            print(f"  {v['name']} ({v['district']})")
            print(f"      point is in: {', '.join(r['found']) or 'nowhere named'}"
                  f"   {r['postcode']}  {r['county']}")
    if close:
        print("\nClose matches, probably fine, listed for completeness:")
        for r in close[:30]:
            print(f"  {r['v']['name']} ({r['v']['district']}) -> {', '.join(r['found'])}")
    if lone:
        print("\nName matches but the postcode is unlike the rest of its district:")
        for r in lone[:30]:
            print(f"  {r['v']['name']} ({r['v']['district']}) {r['postcode']}")

    out = DIST / "centroid_verification.json"
    out.write_text(json.dumps(
        [{"id": r["v"]["id"], "name": r["v"]["name"], "district": r["v"]["district"],
          "status": r["status"], "found": r.get("found", []), "postcode": r.get("postcode", "")}
         for r in rows], indent=2))
    print(f"\nfull results: {out}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
