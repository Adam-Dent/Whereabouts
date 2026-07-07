"""Nominatim geocoding for village centroids with caching (spec §5.4, §5.5)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import requests


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


def geocode_village(village_name: str) -> Optional[tuple[float, float]]:
    """
    Geocode '<village_name>, North Yorkshire' via Nominatim.
    Returns (lat, lng) or None. Results are cached.
    """
    global _last_request

    if not _cache:
        _load_cache()

    query = f"{village_name}, North Yorkshire"
    if query in _cache:
        return _cache[query]

    # Rate-limit
    elapsed = time.time() - _last_request
    if elapsed < _RATE_LIMIT_S:
        time.sleep(_RATE_LIMIT_S - elapsed)

    resp = requests.get(
        _NOMINATIM,
        params={"q": query, "format": "json", "limit": 1},
        headers={"User-Agent": "Whereabouts-ETL/0.1 (github placeholder)"},
        timeout=10,
    )
    _last_request = time.time()
    resp.raise_for_status()

    results = resp.json()
    if results:
        result = (float(results[0]["lat"]), float(results[0]["lon"]))
    else:
        result = None

    _cache[query] = result
    _save_cache()
    return result
