"""Invariants over the real dataset, all 42,444 records of it.

This is the file that answers "how do you know the data is right". It does not
check that any individual house is in the correct field: only the hand placement
and Colin's maps can say that. What it does check is every property the app
relies on and the ETL is supposed to guarantee, over the actual shipped data
rather than a sample. A parser change that corrupts a fraction of a percent of
records is invisible in the coverage report and obvious here.

These run against built artefacts and skip if they are absent, so a fresh
checkout without a build still gets a green suite.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from etl.transform import coords_in_north_yorkshire


# ── Shape and identity ───────────────────────────────────────────────────────

def test_shipped_payload_has_the_keys_the_app_reads(shipped: dict) -> None:
    assert set(shipped) >= {"v", "generated", "sheets", "districts", "houses"}
    assert shipped["v"] == 2
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", shipped["generated"])


def test_house_ids_are_unique(shipped: dict) -> None:
    """Duplicate ids would make search results ambiguous and, worse, make
    placements land on whichever record happened to be found first."""
    ids = [h["id"] for h in shipped["houses"]]
    dupes = [i for i, n in Counter(ids).items() if n > 1]
    assert not dupes, f"{len(dupes)} duplicate house ids, e.g. {dupes[:5]}"


def test_every_house_belongs_to_a_sheet_that_exists(shipped: dict) -> None:
    """The app derives the sheet id by splitting the house id at its last hyphen
    (`sheetOf` in pwa.py). An orphan means a house with no map to show."""
    sheets = shipped["sheets"]
    orphans = [h["id"] for h in shipped["houses"] if h["id"].rsplit("-", 1)[0] not in sheets]
    assert not orphans, f"{len(orphans)} houses with no sheet, e.g. {orphans[:5]}"


def test_every_house_id_ends_in_its_map_number(shipped: dict) -> None:
    bad = [h["id"] for h in shipped["houses"] if not h["id"].rsplit("-", 1)[1].isdigit()]
    assert not bad, f"{len(bad)} house ids without a numeric map number, e.g. {bad[:5]}"


def test_every_sheet_belongs_to_a_known_district(shipped: dict) -> None:
    districts = set(shipped["districts"])
    bad = [sid for sid, s in shipped["sheets"].items() if s["district"] not in districts]
    assert not bad, f"sheets in unknown districts: {bad[:5]}"


# ── Names ────────────────────────────────────────────────────────────────────

def test_every_shipped_house_has_at_least_one_non_empty_name(shipped: dict) -> None:
    """The PWA drops nameless legend gaps on the way out. Anything nameless that
    survived that filter would be an unsearchable row taking up space."""
    bad = [h["id"] for h in shipped["houses"] if not [n for n in h["n"] if n and n.strip()]]
    assert not bad, f"{len(bad)} shipped houses with no usable name, e.g. {bad[:5]}"


def test_no_more_parser_debris_reaches_the_app(shipped: dict) -> None:
    """Catches the ".0 1 .- 2" class of mis-parse reaching the app.

    26 records currently ship with a primary name that is nothing but digits
    ("2", "122"): a legend number read as its own name. They are unsearchable
    and show as a bare number in results. Pinned rather than tolerated, so the
    count cannot creep up while nobody is looking.
    """
    from etl.parse import _is_junk_name

    KNOWN_JUNK = 26
    bad = [(h["id"], h["n"][0]) for h in shipped["houses"] if _is_junk_name(h["n"][0])]
    assert len(bad) <= KNOWN_JUNK, (
        f"{len(bad)} junk primary names shipped, up from {KNOWN_JUNK}: {bad[:5]}"
    )


def test_alias_lists_have_no_duplicates(shipped: dict) -> None:
    bad = [h["id"] for h in shipped["houses"] if len(set(h["n"])) != len(h["n"])]
    assert not bad, f"{len(bad)} houses with a repeated name, e.g. {bad[:5]}"


def test_shipped_aliases_all_pass_the_alias_rule(shipped: dict) -> None:
    """Guards the Carlton/Ivelet merge fix at the dataset level: if the rule ever
    regresses, garbage aliases show up here rather than in the app."""
    from etl.parse import _acceptable_alias

    bad = [
        (h["id"], alias)
        for h in shipped["houses"]
        for alias in h["n"][1:]
        if not _acceptable_alias(alias)
    ]
    assert not bad, f"{len(bad)} unacceptable aliases shipped, e.g. {bad[:5]}"


# ── Positions ────────────────────────────────────────────────────────────────

def test_placed_houses_have_both_coordinates(shipped: dict) -> None:
    """Half a coordinate sends someone to the equator."""
    bad = [
        h["id"] for h in shipped["houses"]
        if (h.get("lat") is None) != (h.get("lng") is None)
    ]
    assert not bad, f"{len(bad)} houses with only one of lat/lng, e.g. {bad[:5]}"


def test_every_placed_house_is_inside_north_yorkshire(shipped: dict) -> None:
    """A transposed lat/lng or a bad affine fit puts houses in the North Sea.
    This is the check that would have caught it before anyone drove there."""
    bad = [
        (h["id"], h["lat"], h["lng"])
        for h in shipped["houses"]
        if h.get("lat") is not None and not coords_in_north_yorkshire(h["lat"], h["lng"])
    ]
    assert not bad, f"{len(bad)} houses outside North Yorkshire, e.g. {bad[:3]}"


def test_coordinates_are_finite_numbers(shipped: dict) -> None:
    bad = [
        h["id"] for h in shipped["houses"]
        if h.get("lat") is not None
        and not (math.isfinite(h["lat"]) and math.isfinite(h["lng"]))
    ]
    assert not bad, f"{len(bad)} houses with non-finite coordinates, e.g. {bad[:5]}"


def test_ring_positions_are_not_grossly_off_their_sheet(shipped: dict) -> None:
    """The ring is drawn at (x, y) scaled against the image's own dimensions.

    A position slightly outside the image is legitimate and expected: Colin
    draws the village, and an outlying farm in the legend can sit beyond the
    edge of the drawing, so its ring is simply clipped. A position a full image
    width or height outside is not legitimate; it means the placement was made
    against the wrong sheet or with a broken transform.
    """
    KNOWN_BAD = {
        # A full image-width off to the left, so these four were placed against
        # something other than their own sheet. Worth revisiting in the tool.
        "healaugh-1", "healaugh-2", "healaugh-3", "healaugh-4",
        "brompton-by-sawdon-east-12",
    }
    sheets = shipped["sheets"]
    bad = set()
    for h in shipped["houses"]:
        if h.get("x") is None:
            continue
        s = sheets.get(h["id"].rsplit("-", 1)[0])
        if not s or not s.get("w"):
            continue
        if not (-s["w"] <= h["x"] <= 2 * s["w"] and -s["h"] <= h["y"] <= 2 * s["h"]):
            bad.add(h["id"])
    new = sorted(bad - KNOWN_BAD)
    fixed = sorted(KNOWN_BAD - bad)
    assert not new, f"ring positions newly off their map: {new}"
    assert not fixed, f"placements fixed but still in KNOWN_BAD, remove them: {fixed}"


def test_village_centroids_agree_with_the_houses_placed_on_that_sheet(
    shipped: dict,
) -> None:
    """The centroid is the directions fallback for every unplaced house, so a
    wrong one silently sends people to a different village.

    Centroids come from Nominatim, looked up by village name alone, and North
    Yorkshire is full of repeated village names (Carlton, Dalton, Angram,
    Newbiggin). Where a sheet has enough hand-placed houses, those houses are
    ground truth and the centroid can be checked against them. Sheets with few
    placements are skipped because a handful of outlying farms is not a reliable
    centre.

    KNOWN_BAD records the sheets already known to be wrong (see
    CLAUDE.local.md). They are listed rather than tolerated silently, so this
    test fails both when a new one appears and when a listed one is fixed
    without the list being updated.
    """
    import statistics as st

    KNOWN_BAD = {
        "carlton", "carlton-wensleydale", "angram", "dalton",
        "melmerby-wensleydale", "hornby", "newbiggin-wensleydale", "grinton",
    }
    MAX_M = 3000.0

    sheets = shipped["sheets"]
    placed: dict[str, list[tuple[float, float]]] = {}
    for h in shipped["houses"]:
        if h.get("lat") is None:
            continue
        placed.setdefault(h["id"].rsplit("-", 1)[0], []).append((h["lat"], h["lng"]))

    wrong = set()
    for sid, pts in placed.items():
        s = sheets.get(sid)
        if not s or s.get("clat") is None or len(pts) < 5:
            continue
        mlat = st.median(p[0] for p in pts)
        mlng = st.median(p[1] for p in pts)
        metres = math.hypot(
            (mlat - s["clat"]) * 111_320.0,
            (mlng - s["clng"]) * 111_320.0 * math.cos(math.radians(mlat)),
        )
        if metres > MAX_M:
            wrong.add(sid)

    new = sorted(wrong - KNOWN_BAD)
    fixed = sorted(KNOWN_BAD - wrong)
    assert not new, f"village centroids newly wrong: {new}"
    assert not fixed, f"centroids fixed but still in KNOWN_BAD, remove them: {fixed}"


def test_district_placed_counts_match_the_houses(shipped: dict) -> None:
    """The coverage bars in the app read from `districts`, so if these disagree
    with the house records the progress shown to users is a lie."""
    sheets = shipped["sheets"]
    counted: Counter = Counter()
    totals: Counter = Counter()
    for h in shipped["houses"]:
        s = sheets.get(h["id"].rsplit("-", 1)[0])
        if not s:
            continue
        totals[s["district"]] += 1
        if h.get("lat") is not None:
            counted[s["district"]] += 1
    for name, d in shipped["districts"].items():
        assert d["total"] == totals[name], f"{name}: total {d['total']} vs {totals[name]} houses"
        assert d["placed"] == counted[name], f"{name}: placed {d['placed']} vs {counted[name]} placed"


# ── Sheets and images ────────────────────────────────────────────────────────

def test_every_sheet_has_usable_image_metadata(shipped: dict) -> None:
    bad = [
        sid for sid, s in shipped["sheets"].items()
        if not s.get("img") or not s.get("w") or not s.get("h")
    ]
    assert not bad, f"{len(bad)} sheets with no usable image metadata, e.g. {bad[:5]}"


def test_every_referenced_image_file_exists(shipped: dict) -> None:
    """A missing file is a broken map in the field, on a phone, with no signal,
    which is exactly the failure the app cannot recover from."""
    from conftest import DOCS

    missing = [
        s["img"] for s in shipped["sheets"].values()
        if s.get("img") and not (DOCS / "images" / s["img"]).exists()
    ]
    assert not missing, f"{len(missing)} referenced images missing, e.g. {missing[:5]}"


def test_no_more_sheets_lose_their_village_centroid(shipped: dict) -> None:
    """Unplaced houses fall back to the village centroid for directions, so a
    sheet without one has nowhere to send anybody.

    25 sheets currently have none: Nominatim could not find the hamlet by name.
    That is a known gap rather than a regression, so this pins the count and
    fails if it grows.
    """
    KNOWN_MISSING = 25
    bad = [sid for sid, s in shipped["sheets"].items() if s.get("clat") is None]
    assert len(bad) <= KNOWN_MISSING, (
        f"{len(bad)} sheets now have no centroid, up from {KNOWN_MISSING}: {bad[:5]}"
    )


def test_every_centroid_is_inside_north_yorkshire(shipped: dict) -> None:
    bad = [
        (sid, s["clat"], s["clng"]) for sid, s in shipped["sheets"].items()
        if s.get("clat") is not None and not coords_in_north_yorkshire(s["clat"], s["clng"])
    ]
    assert not bad, f"{len(bad)} centroids outside North Yorkshire, e.g. {bad[:3]}"


# ── Shipped vs parsed ────────────────────────────────────────────────────────

def test_the_only_records_dropped_on_the_way_out_are_nameless(
    shipped: dict, parsed_houses: list[dict]
) -> None:
    """The parse yields more records than the app ships. That difference must be
    entirely explained by nameless legend gaps, and nothing else: any other gap
    means the build is losing real houses."""
    shipped_ids = {h["id"] for h in shipped["houses"]}
    dropped = [h for h in parsed_houses if h["id"] not in shipped_ids]
    with_names = [
        h["id"] for h in dropped if [n for n in h.get("names", []) if n and n.strip()]
    ]
    assert not with_names, (
        f"{len(with_names)} named houses were parsed but not shipped, e.g. {with_names[:5]}"
    )
