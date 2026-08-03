"""Nominatim geocoding for village centroids with caching (spec §5.4, §5.5)."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

import requests

from . import USER_AGENT
from .transform import coords_in_north_yorkshire


_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_CACHE_FILE = Path(__file__).parent.parent.parent.parent / "data" / ".cache" / "geocode.json"
_RATE_LIMIT_S = 1.1   # Nominatim: max 1 req/s

_cache: dict[str, Optional[tuple[float, float]]] = {}
_last_request = 0.0


def _load_cache() -> None:
    if _CACHE_FILE.exists():
        raw = json.loads(_CACHE_FILE.read_text())
        _cache.update({k: tuple(v) if v else None for k, v in raw.items()})


def _save_cache() -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps({k: list(v) if v else None for k, v in _cache.items()}, indent=2))


def geocode_village(
    village_name: str, district: str | None = None
) -> Optional[tuple[float, float]]:
    """Geocode a village via Nominatim. Returns (lat, lng) or None, cached.

    `district` matters more than it looks. North Yorkshire has several villages
    called Carlton, and more than one Dalton, Angram, Melmerby, Hornby,
    Newbiggin and Grinton. Asking for the name alone returns whichever ranks
    first and there is no way to tell it apart from the right one, which put
    Carlton's centroid 98km from Carlton. Because the cache is keyed on the
    query, two different villages sharing a name also shared a single answer,
    so getting one wrong got both wrong.

    The result is also checked against the county bounds before it is accepted:
    a lookup that lands outside North Yorkshire is wrong by definition here, and
    a null centroid is safer than a confidently wrong one, because the app can
    tell the user it has no location but cannot tell that a location is a lie.
    """
    global _last_request

    if not _cache:
        _load_cache()

    # Dale names ("Wensleydale") disambiguate well because Nominatim knows them;
    # administrative districts ("Hambleton (West)") less so, but the bracketed
    # qualifier is noise either way, so it is dropped.
    place = re.sub(r"\s*\([^)]*\)", "", district).strip() if district else ""
    query = f"{village_name}, {place}, North Yorkshire" if place else f"{village_name}, North Yorkshire"
    if query in _cache:
        return _cache[query]

    # Rate-limit
    elapsed = time.time() - _last_request
    if elapsed < _RATE_LIMIT_S:
        time.sleep(_RATE_LIMIT_S - elapsed)

    resp = requests.get(
        _NOMINATIM,
        params={"q": query, "format": "json", "limit": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=10,
    )
    _last_request = time.time()
    resp.raise_for_status()

    results = resp.json()
    result = None
    if results:
        lat, lng = float(results[0]["lat"]), float(results[0]["lon"])
        if coords_in_north_yorkshire(lat, lng):
            result = (lat, lng)
        else:
            print(f"  geocode rejected: {query} -> {lat},{lng} is outside North Yorkshire")

    _cache[query] = result
    _save_cache()
    return result
