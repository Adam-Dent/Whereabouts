"""Parser edge cases, each one a bug that actually happened.

Every test here corresponds to a fix documented in README.md's war stories or in
a comment in parse.py. They are the regressions that would otherwise be silent:
the parser would keep running, keep emitting records, and quietly produce worse
data across 865 sheets with nobody the wiser until someone drove to the wrong
house.

The parser works on pdfplumber's word and char dicts, and the helpers under test
are pure functions of those, so these build the dicts directly (see `word` in
conftest) rather than carrying PDF fixtures around. Tests that need a real PDF
would be testing pdfplumber, not this code.
"""

from __future__ import annotations

import pytest

from etl.parse import (
    _acceptable_alias,
    _cluster_by_top,
    _date_integer_ids,
    _is_junk_name,
    _is_spaced_fragment,
    _legend_row_words,
    _reconstruct,
)

from conftest import word


# ── Row clustering ───────────────────────────────────────────────────────────

def test_cluster_by_top_groups_a_row_despite_sub_pixel_drift() -> None:
    items = [word("Bridge", 10, 100.0), word("House", 45, 100.7), word("Farm", 10, 140.0)]
    lines = _cluster_by_top(items)
    assert [len(ln) for ln in lines] == [2, 1]


def test_cluster_by_top_handles_no_items() -> None:
    assert _cluster_by_top([]) == []


# ── Word reconstruction, and Eppleby's 2pt gap ───────────────────────────────

def test_reconstruct_inserts_spaces_on_word_gaps() -> None:
    chars = [word("B", 10, 100, width=5), word("H", 20, 100, width=5)]
    assert _reconstruct(chars) == "B H"


def test_reconstruct_keeps_tight_kerning_as_one_word() -> None:
    # Adjacent letters within _WORD_GAP must not be split, or every name would
    # come out letter-spaced.
    chars = [word("H", 10, 100, width=5), word("i", 16, 100, width=5)]
    assert _reconstruct(chars) == "Hi"


def test_reconstruct_drops_dot_leaders() -> None:
    chars = [
        word("Church", 10, 100, width=20),
        word(".", 35, 100, width=2),
        word(".", 39, 100, width=2),
        word("7", 60, 100, width=5),
    ]
    assert _reconstruct(chars) == "Church 7"


def test_reconstruct_stops_at_an_oversized_gap() -> None:
    """Eppleby: a stray map label sitting on the same row as a legend entry.

    The label is far to the right, past `max_gap`. Without the cut-off it merged
    into the name and produced "Bridge House 14".
    """
    chars = [
        word("Bridge", 10, 100, width=25),
        word("House", 38, 100, width=22),
        word("14", 300, 100, width=8),
    ]
    assert _reconstruct(chars, max_gap=20.0) == "Bridge House"
    # Without the bound, the stray label merges in: this is the bug.
    assert _reconstruct(chars) == "Bridge House 14"


# ── Junk and fragment rejection ──────────────────────────────────────────────

@pytest.mark.parametrize("junk", ["", "   ", ".0 1 .- 2", "...", "- - -", "12 34", ".-"])
def test_is_junk_name_rejects_legend_debris(junk: str) -> None:
    assert _is_junk_name(junk)


@pytest.mark.parametrize("real", ["7 Manor Way", "Church", "1 Eryholme Lane", "The Old Rectory"])
def test_is_junk_name_keeps_real_names_including_numeric_addresses(real: str) -> None:
    # "7 Manor Way" is a real address and must survive; only strings with no
    # letters at all are junk.
    assert not _is_junk_name(real)


@pytest.mark.parametrize("frag", [".C", "r.", "h.", "c", "A"])
def test_is_spaced_fragment_catches_letter_spaced_bleed(frag: str) -> None:
    assert _is_spaced_fragment(frag)


@pytest.mark.parametrize("keep", ["Church", "The", "Farm", "1", "12", "..."])
def test_is_spaced_fragment_leaves_real_words_and_pure_punctuation(keep: str) -> None:
    assert not _is_spaced_fragment(keep)


# ── Cross-reference aliases, and the Carlton/Ivelet merge ────────────────────

@pytest.mark.parametrize(
    "alias",
    ["Church", "St Mary's Church", "1 Eryholme Lane", "Old Hall"],
)
def test_acceptable_alias_keeps_real_aliases(alias: str) -> None:
    assert _acceptable_alias(alias)


@pytest.mark.parametrize(
    ("alias", "why"),
    [
        ("2 Baygante Carlton Boarding Kennels", "two dot-leader lines merged (Carlton)"),
        ("11 Thirley Cottage Satron Farm", "two dot-leader lines merged (Ivelet)"),
        ("Church 7 Manor Way", "more than one integer token"),
        ("Manor Way 7", "integer that is not a leading house number"),
        ("59MoorRoad", "number and name concatenated, space lost"),
        ("6ScotsDyke", "number and name concatenated, space lost"),
        ("", "empty"),
        ("A" * 41, "over the 40-character bound"),
    ],
)
def test_acceptable_alias_rejects_merge_garbage(alias: str, why: str) -> None:
    assert not _acceptable_alias(alias), why


def test_acceptable_alias_boundary_is_three_words() -> None:
    assert _acceptable_alias("One Two Three")
    assert not _acceptable_alias("One Two Three Four")


# ── Date lines vs legend numbers ─────────────────────────────────────────────

def test_date_integer_ids_finds_the_revision_date_day() -> None:
    """"9 January 2011" in the header: the 9 must not become legend entry 9."""
    line = [word("9", 10, 20), word("January", 20, 20), word("2011", 70, 20)]
    ids = _date_integer_ids(line)
    assert id(line[0]) in ids
    assert id(line[2]) in ids


def test_date_integer_ids_ignores_a_month_word_without_a_year() -> None:
    """A house called "May House" has a month word but no 4-digit year.

    Requiring both is what stops a real house name being read as a date line and
    having its legend number thrown away.
    """
    line = [word("4", 10, 300), word("May", 20, 300), word("House", 50, 300)]
    assert _date_integer_ids(line) == set()


def test_date_integer_ids_ignores_a_year_like_number_without_a_month() -> None:
    line = [word("12", 10, 300), word("Beckside", 25, 300), word("2011", 80, 300)]
    assert _date_integer_ids(line) == set()


# ── Legend row assembly ──────────────────────────────────────────────────────

def test_legend_row_words_stops_before_the_next_legend_column() -> None:
    """Wide legends pack several entries per row.

    Without the column bound, entry 1's name swallowed entry 2's number and name.
    """
    num = word("1", 10, 100, width=5)
    words = [
        num,
        word("Bridge", 18, 100, width=25),
        word("House", 46, 100, width=22),
        word("2", 200, 100, width=5),          # next legend column
        word("Rose", 208, 100, width=18),
        word("Cottage", 228, 100, width=28),
    ]
    got = _legend_row_words(num, words, col_x0s=[10.0, 200.0])
    assert [w["text"] for w in got] == ["Bridge", "House"]


def test_legend_row_words_stops_at_a_merged_map_label() -> None:
    num = word("1", 10, 100, width=5)
    words = [num, word("Bridge", 18, 100, width=25), word("47", 300, 100, width=8)]
    got = _legend_row_words(num, words, col_x0s=[10.0])
    assert [w["text"] for w in got] == ["Bridge"]


def test_legend_row_words_ignores_rows_above_and_below() -> None:
    num = word("1", 10, 100, width=5)
    words = [num, word("Bridge", 18, 100, width=25), word("Elsewhere", 18, 160, width=35)]
    got = _legend_row_words(num, words, col_x0s=[10.0])
    assert [w["text"] for w in got] == ["Bridge"]
