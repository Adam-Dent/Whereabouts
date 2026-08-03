"""Slug and name-normalisation rules (spec 6.1, 6.5).

These are load-bearing in a way that is easy to miss: `slugify` output becomes
the sheet id, which becomes the house id, which becomes the image filename and
the key in every placements file. A change here silently orphans thousands of
hand-placed houses, so the cases below pin the behaviour rather than describe
it. `normalize_name` is reimplemented in JavaScript in the PWA (`norm()`), and
test_pages.py checks the two agree.
"""

from __future__ import annotations

import pytest

from etl.slugs import house_id, normalize_name, slugify


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Aldborough St John", "aldborough-st-john"),
        ("Swaledale and Arkengarthdale", "swaledale-and-arkengarthdale"),
        ("Hambleton (West)", "hambleton-west"),
        ("Brompton-by-Sawdon South", "brompton-by-sawdon-south"),
        ("  Leading and trailing  ", "leading-and-trailing"),
        ("Multiple   spaces", "multiple-spaces"),
        ("St Mary's", "st-mary-s"),
        ("Appleton-le-Moors", "appleton-le-moors"),
    ],
)
def test_slugify(raw: str, expected: str) -> None:
    assert slugify(raw) == expected


def test_slugify_strips_accents_rather_than_dropping_the_word() -> None:
    # An accented place name must not collapse to an empty or truncated slug.
    assert slugify("Café Cottage") == "cafe-cottage"


def test_slugify_is_idempotent() -> None:
    # Re-slugging an id must not change it, or ids drift on any code path that
    # slugifies twice.
    for name in ("Aldborough St John", "Hambleton (West)", "St Mary's"):
        once = slugify(name)
        assert slugify(once) == once


def test_house_id_shape_matches_what_the_pwa_splits_on() -> None:
    # pwa.py's sheetOf() recovers the sheet id with h.id.slice(0, lastIndexOf('-')),
    # so the map number must be the only thing after the final hyphen.
    hid = house_id("aldborough-st-john-north", 12)
    assert hid == "aldborough-st-john-north-12"
    assert hid.rsplit("-", 1)[0] == "aldborough-st-john-north"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("The Old Rectory", "the old rectory"),
        ("St Mary’s Church", "st mary s church"),   # curly apostrophe
        ("St Mary's Church", "st mary s church"),        # straight, same result
        ("7 Manor Way", "7 manor way"),                  # digits survive
        ("Beck-Side Cottage", "beck side cottage"),
        ("  Padded   Name  ", "padded name"),
        ("Café", "cafe"),  # NFKD splits the accent off, so the letter survives
    ],
)
def test_normalize_name(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


def test_normalize_name_makes_apostrophe_styles_searchable_alike() -> None:
    # Colin's PDFs use both. If these diverged, half the possessive house names
    # would be unfindable depending on which character the sheet happened to use.
    assert normalize_name("St Mary’s") == normalize_name("St Mary's")
