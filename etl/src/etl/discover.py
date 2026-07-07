"""PDF discovery: scrape index pages and build sheet metadata (spec §5.1)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from .models import Sheet, ImageSize
from .slugs import slugify


# Default discovery index URLs (one per district).
# All North Yorkshire districts, per https://colinday.co.uk/maps/NorthYorks.shtml.
# Coverage is being rolled out one district at a time (see data/dist/coverage.md) —
# a district appearing here doesn't mean it's been processed yet; run with
# `--district "<name>"` to process a single one, then check the report before
# moving to the next.
DISTRICT_INDEXES: dict[str, str] = {
    "Richmondshire": "https://colinday.co.uk/maps/Richmondshire.shtml",
    "Swaledale and Arkengarthdale": "https://colinday.co.uk/maps/Swaledale.shtml",
    "Craven": "https://colinday.co.uk/maps/Craven.shtml",
    "Hambleton (East)": "https://colinday.co.uk/maps/HambletonEast.shtml",
    "Hambleton (West)": "https://colinday.co.uk/maps/Hambleton.shtml",
    "Harrogate": "https://colinday.co.uk/maps/Harrogate.shtml",
    "Ryedale (East)": "https://colinday.co.uk/maps/RyedaleEast.shtml",
    "Ryedale (West)": "https://colinday.co.uk/maps/Ryedale.shtml",
    "Scarborough": "https://colinday.co.uk/maps/Scarborough.shtml",
    "Selby": "https://colinday.co.uk/maps/Selby.shtml",
    "Wensleydale": "https://colinday.co.uk/maps/Wensleydale.shtml",
}

_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = "Whereabouts-ETL/0.1 (github placeholder)"

CACHE_DIR = Path(__file__).parent.parent.parent.parent / "data" / ".cache" / "pdfs"


def _normalize_url(url: str) -> str:
    """Force https, strip www. inconsistency."""
    parsed = urlparse(url)
    host = parsed.netloc.lstrip("www.")
    return parsed._replace(scheme="https", netloc=host).geturl()


def _parse_anchor_text(text: str) -> tuple[str, str | None]:
    """
    Extract village name and optional sheet discriminator from anchor text.
    E.g. "Catterick Village (NE) 9/23" -> ("Catterick Village", "NE")
         "Eryholme 1/11"               -> ("Eryholme", None)
    """
    # Strip trailing revision date like "1/11" or "9/23"
    text = re.sub(r"\s+\d+/\d+\s*$", "", text).strip()
    # Extract bracketed discriminator
    m = re.search(r"\(([^)]+)\)\s*$", text)
    if m:
        discriminator = m.group(1).strip()
        village_name = text[: m.start()].strip()
        return village_name, discriminator
    return text, None


def _build_sheet_id(village_name: str, discriminator: str | None) -> str:
    base = slugify(village_name)
    if discriminator:
        return f"{base}-{slugify(discriminator)}"
    return base


def discover_sheets(
    district_indexes: dict[str, str] | None = None,
    cache_dir: Path | None = None,
    existing_ids: set[str] | None = None,
) -> list[Sheet]:
    """Download index pages and return Sheet stubs (no parsing yet).

    `existing_ids` are sheet ids already emitted by *other* districts (passed by
    main.py from the current data/dist/sheets.json). Village names aren't unique
    across North Yorkshire — Newby, Carlton, Kirkby etc. all recur — so without
    this check a same-named village in a second district silently overwrites the
    first one's sheet under the shared un-discriminated id (this happened to
    Craven's Newby when Hambleton (East) was processed next, both slugifying to
    plain "newby"). Colliding ids get the district appended to disambiguate.
    """
    if district_indexes is None:
        district_indexes = DISTRICT_INDEXES
    if cache_dir is None:
        cache_dir = CACHE_DIR
    if existing_ids is None:
        existing_ids = set()
    cache_dir.mkdir(parents=True, exist_ok=True)

    sheets: list[Sheet] = []
    seen_urls: set[str] = set()
    seen_ids: set[str] = set(existing_ids)

    for district, index_url in district_indexes.items():
        print(f"Discovering {district} from {index_url}")
        resp = _SESSION.get(index_url, timeout=30)
        resp.raise_for_status()
        html = resp.text

        # Find all hrefs ending in .pdf
        for m in re.finditer(r'href=["\']([^"\']*\.pdf)["\']', html, re.IGNORECASE):
            raw_url = m.group(1)
            pdf_url = _normalize_url(urljoin(index_url, raw_url))
            if pdf_url in seen_urls:
                continue
            seen_urls.add(pdf_url)

            # Try to get the anchor text for the village name
            # Look backwards from this match for the opening <a tag
            start = html.rfind("<a ", 0, m.start())
            end = html.find("</a>", m.start())
            anchor_html = html[start : end + 4] if start >= 0 and end >= 0 else ""
            text_m = re.search(r">([^<]+)</a>", anchor_html)
            anchor_text = text_m.group(1).strip() if text_m else Path(raw_url).stem

            village_name, discriminator = _parse_anchor_text(anchor_text)
            sheet_id = _build_sheet_id(village_name, discriminator)
            village_id = slugify(village_name)

            if sheet_id in seen_ids:
                sheet_id = f"{sheet_id}-{slugify(district)}"
                village_id = f"{village_id}-{slugify(district)}"
                print(f"  NOTE: '{village_name}' collides with an existing sheet id — "
                      f"disambiguated to '{sheet_id}'")
            seen_ids.add(sheet_id)

            sheet = Sheet(
                id=sheet_id,
                village_id=village_id,
                village_name=village_name,
                district=district,
                pdf_url=pdf_url,
                pdf_hash="",           # filled by download step
                image_path=f"images/{sheet_id}.png",
                image_size=ImageSize(w=0, h=0),   # filled by render step
                affine=None,
                georef_residual_m=None,
                control_point_count=0,
            )
            sheets.append(sheet)

    print(f"Discovered {len(sheets)} sheets across {len(district_indexes)} district(s)")
    return sheets


def download_pdf(sheet: Sheet, cache_dir: Path | None = None) -> tuple[Path, str]:
    """Download the PDF for a sheet, returning (local_path, sha256_hex)."""
    if cache_dir is None:
        cache_dir = CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)

    filename = cache_dir / f"{sheet.id}.pdf"
    resp = _SESSION.get(sheet.pdf_url, timeout=60, stream=True)
    resp.raise_for_status()

    h = hashlib.sha256()
    with open(filename, "wb") as f:
        for chunk in resp.iter_content(65536):
            f.write(chunk)
            h.update(chunk)

    pdf_hash = h.hexdigest()
    return filename, pdf_hash
