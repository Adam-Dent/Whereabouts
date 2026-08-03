"""Reading Colin's index pages, and choosing between villages of the same name.

Sheet ids come from the anchor text on Colin's district pages, and a sheet id is
permanent: it becomes the house id, the image filename and the key in every
placements file. Getting one wrong once orphans hand-placed work that cannot be
regenerated, so the parsing is pinned rather than described.

Nothing here touches the network; conftest fails the test if anything tries.
"""

from __future__ import annotations

import pytest

from etl.discover import _build_sheet_id, _normalize_url, _parse_anchor_text
from etl.geocode import _rough_metres


# ── Anchor text on Colin's index pages ───────────────────────────────────────

@pytest.mark.parametrize(
    ("anchor", "village", "discriminator"),
    [
        ("Eryholme 1/11", "Eryholme", None),
        ("Catterick Village (NE) 9/23", "Catterick Village", "NE"),
        ("Aldborough St John (North) 3/19", "Aldborough St John", "North"),
        ("Brompton-by-Sawdon (South) 12/22", "Brompton-by-Sawdon", "South"),
        ("Muker", "Muker", None),
        ("Hawes (West) 1/1", "Hawes", "West"),
    ],
)
def test_anchor_text_splits_into_village_and_sheet(
    anchor: str, village: str, discriminator: str | None
) -> None:
    assert _parse_anchor_text(anchor) == (village, discriminator)


def test_a_revision_date_is_stripped_wherever_it_appears() -> None:
    """The trailing "9/23" is Colin's revision date, not part of the name."""
    assert _parse_anchor_text("Reeth 11/24")[0] == "Reeth"
    assert _parse_anchor_text("Reeth   11/24  ")[0] == "Reeth"


def test_a_bracket_that_is_not_at_the_end_is_left_alone() -> None:
    """Only a trailing bracket is a sheet discriminator. Anything else is part
    of the village's actual name and must survive."""
    name, disc = _parse_anchor_text("Newton (le Willows) Grange 4/12")
    assert disc is None
    assert name == "Newton (le Willows) Grange"


# ── Sheet ids ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("village", "discriminator", "expected"),
    [
        ("Eryholme", None, "eryholme"),
        ("Catterick Village", "NE", "catterick-village-ne"),
        ("Aldborough St John", "North", "aldborough-st-john-north"),
        ("Hawes", "West", "hawes-west"),
    ],
)
def test_sheet_ids_are_stable(village: str, discriminator: str | None, expected: str) -> None:
    """These strings are load-bearing: they key every placements file on disk.
    A change here silently orphans hand-placed work."""
    assert _build_sheet_id(village, discriminator) == expected


def test_two_sheets_of_one_village_get_distinct_ids() -> None:
    a = _build_sheet_id(*_parse_anchor_text("Hawes (West) 1/1"))
    b = _build_sheet_id(*_parse_anchor_text("Hawes (East) 1/1"))
    assert a != b


# ── URLs ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://colinday.co.uk/maps/x.pdf", "https://colinday.co.uk/maps/x.pdf"),
        ("https://colinday.co.uk/maps/x.pdf", "https://colinday.co.uk/maps/x.pdf"),
    ],
)
def test_urls_are_normalised_to_https(raw: str, expected: str) -> None:
    """Colin's pages mix http and https links to the same file. Without this,
    the same PDF downloads twice under two identities."""
    assert _normalize_url(raw) == expected


# ── Choosing between villages of the same name ───────────────────────────────

def test_rough_metres_matches_a_known_distance() -> None:
    # One hundredth of a degree of latitude is about 1.113 km anywhere.
    assert _rough_metres((54.40, -1.80), (54.41, -1.80)) == pytest.approx(1113, abs=15)


def test_rough_metres_is_symmetric() -> None:
    a, b = (54.40, -1.80), (54.47, -1.83)
    assert _rough_metres(a, b) == pytest.approx(_rough_metres(b, a), rel=1e-3)


def test_the_nearest_candidate_is_the_right_carlton() -> None:
    """The real case that started this. "Carlton, North Yorkshire" returns six
    places; the first is near Selby and is the wrong one for both Carltons on
    Colin's maps. Given roughly where the district is, the list resolves."""
    candidates = [
        (53.7147, -1.0175),   # Carlton near Selby, what Nominatim returned first
        (54.2574, -1.9042),   # Carlton in Coverdale, the Wensleydale sheet
        (54.5056, -1.7020),   # Carlton near Stanwick, the Richmondshire sheet
    ]
    richmondshire = (54.47, -1.82)
    wensleydale = (54.28, -1.92)

    assert min(candidates, key=lambda c: _rough_metres(c, richmondshire)) == (54.5056, -1.7020)
    assert min(candidates, key=lambda c: _rough_metres(c, wensleydale)) == (54.2574, -1.9042)
