"""Nominatim geocoding for village centroids with caching (spec §5.4, §5.5)."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Optional

import requests

from . import USER_AGENT
from .transform import coords_in_north_yorkshire


_NOMINATIM = "https://nominatim.openstreetmap.org/search"
# Its own file: the old geocode.json held a single best guess per query, this
# holds every candidate, and silently reading one as the other would resurrect
# exactly the wrong answers this replaced.
_CACHE_FILE = Path(__file__).parent.parent.parent.parent / "data" / ".cache" / "geocode_candidates.json"
_RATE_LIMIT_S = 1.1   # Nominatim: max 1 req/s

_cache: dict[str, list[tuple[float, float]]] = {}
_last_request = 0.0


def _load_cache() -> None:
    if _CACHE_FILE.exists():
        raw = json.loads(_CACHE_FILE.read_text())
        _cache.update({k: [tuple(c) for c in v] for k, v in raw.items()})


def _save_cache() -> None:
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(
        json.dumps({k: [list(c) for c in v] for k, v in _cache.items()}, indent=2)
    )


def village_candidates(village_name: str) -> list[tuple[float, float]]:
    """Every plausible North Yorkshire location for a village name, cached.

    Asking Nominatim for one result and taking it is what put Carlton's centroid
    98km from Carlton. "Carlton, North Yorkshire" has six matches and the first
    is the one near Selby, which is the wrong Carlton for both of the Carltons
    on Colin's maps. Qualifying the query does not fix it either: Richmondshire
    administratively contains Wensleydale, so "Carlton, Richmondshire" returns
    the Coverdale one.

    So this returns the whole candidate list and leaves the choice to a caller
    that has some idea where the village should be. Results outside the county
    are dropped here, because they cannot be the answer whatever the caller
    thinks.
    """
    global _last_request

    if not _cache:
        _load_cache()

    query = f"{village_name}, North Yorkshire"
    if query in _cache:
        return _cache[query] or []

    elapsed = time.time() - _last_request
    if elapsed < _RATE_LIMIT_S:
        time.sleep(_RATE_LIMIT_S - elapsed)

    resp = requests.get(
        _NOMINATIM,
        params={"q": query, "format": "json", "limit": 10},
        headers={"User-Agent": USER_AGENT},
        timeout=15,
    )
    _last_request = time.time()
    resp.raise_for_status()

    out: list[tuple[float, float]] = []
    for r in resp.json():
        lat, lng = float(r["lat"]), float(r["lon"])
        if coords_in_north_yorkshire(lat, lng) and (lat, lng) not in out:
            out.append((lat, lng))

    _cache[query] = out
    _save_cache()
    return out


def geocode_village(
    village_name: str, near: tuple[float, float] | None = None
) -> Optional[tuple[float, float]]:
    """Best North Yorkshire location for a village name, or None.

    `near` is the disambiguator: given roughly where the village ought to be
    (the middle of the other villages in its district, say), the nearest
    candidate is the right one. Without it, an unambiguous name still resolves,
    but an ambiguous one returns None rather than a guess. That is deliberate:
    the app can tell a user it has no location for a village, but it cannot tell
    that a location is a lie, so a null beats a confident error.
    """
    cands = village_candidates(village_name)
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    if near is None:
        return None
    return min(cands, key=lambda c: _rough_metres(c, near))


def _rough_metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Flat-earth distance. Fine over a county, and avoids a dependency."""
    dlat = (a[0] - b[0]) * 111_320.0
    dlng = (a[1] - b[1]) * 111_320.0 * math.cos(math.radians(a[0]))
    return math.hypot(dlat, dlng)


