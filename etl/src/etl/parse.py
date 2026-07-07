"""PDF parsing: legend extraction and map-label position capture (spec §5.2).

Each sheet has three zones of integer text that must be told apart:

1. The *numbered legend* — a left column of right-aligned numbers, each
   followed by a house name (names may themselves begin with a digit, e.g.
   "6 1 Eryholme Lane").
2. The *alphabetical cross-reference* — "Name ....dot leaders.... Number",
   numbers right-aligned at the page's right margin. Supplies extra aliases
   (Eryholme lists both "Church" and "St Mary's Church" for 9).
3. The *map labels* — isolated numbers scattered across the drawing; their
   centroid is the house's page position.

Whole-page line clustering conflates these (a scattered map label sharing a
`top` band with a legend row leaks in as a fake alias), so we work from the
column geometry and reconstruct text from `page.chars`, dropping the dotted
leaders and splitting words on horizontal gaps.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from .models import PagePos
from .slugs import normalize_name

_INT = re.compile(r"^\d+$")
_MONTHS = {
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
}

# Geometry tolerances (PDF points). These describe the colinday.co.uk village
# map template, which is consistent across sheets.
_ROW_BAND = 5.0          # vertical half-height of a text row
_X1_COL_TOL = 4.0        # tolerance when grouping numbers into a right-aligned column
_NAME_MAX_GAP = 20.0     # a gap wider than this ends a legend name (drops stray labels)
_WORD_GAP = 2.0          # gap wider than this separates words during reconstruction
_DOT_LEADER_MIN = 3      # a cross-reference line carries at least this many dot leaders


@dataclass
class ParsedHouse:
    map_number: int
    names: list[str]
    names_normalized: list[str]
    page_pos: PagePos | None   # centroid of the on-map number label; None when
                               # the house is not numbered on the drawing


@dataclass
class ParseResult:
    sheet_id: str
    houses: list[ParsedHouse]
    label_count_ok: bool   # legend count == map label count
    notes: list[str]       # warnings / discrepancies


def _cluster_by_top(items: list[dict], y_tol: float = 3.0) -> list[list[dict]]:
    """Group words/chars into text lines by proximity of `top`."""
    if not items:
        return []
    lines: list[list[dict]] = []
    current: list[dict] = []
    current_top: float | None = None
    for item in sorted(items, key=lambda c: c["top"]):
        if current_top is None or abs(item["top"] - current_top) <= y_tol:
            current.append(item)
            if current_top is None:
                current_top = item["top"]
        else:
            lines.append(current)
            current = [item]
            current_top = item["top"]
    if current:
        lines.append(current)
    return lines


def _reconstruct(chars: list[dict], max_gap: float | None = None) -> str:
    """Rebuild a text string from chars in x-order.

    Drops dot-leader characters, inserts a space on any gap wider than a normal
    inter-letter kern, and (if `max_gap` is set) stops at the first oversized
    gap — used to trim a stray map label that merged into a legend row.
    """
    tokens = [c for c in sorted(chars, key=lambda c: c["x0"]) if c["text"] != "." and c["text"].strip()]
    out = ""
    prev: dict | None = None
    for c in tokens:
        if prev is not None:
            gap = c["x0"] - prev["x1"]
            if max_gap is not None and gap > max_gap:
                break
            if gap > _WORD_GAP:
                out += " "
        out += c["text"]
        prev = c
    return re.sub(r"\s+", " ", out).strip()


def _legend_like(
    int_words: list[dict],
    words: list[dict],
    chars: list[dict] | None = None,
) -> list[dict]:
    """Integers immediately followed by a name word — i.e. legend entries.

    A legend number has its name just to the right ("12 White Swan Inn", or
    "6 1 Eryholme Lane" where the next word is itself a digit). Map labels are
    isolated and cross-reference numbers sit at a line's end after dot leaders,
    so neither has a word butting up against its right edge.

    When `chars` is supplied, also accepts numbers whose immediate right
    neighbour is a space character — blank legend entries like entry 4 in
    Croft-on-Tees that have no house name but must not break the sequence.
    """
    out: list[dict] = []
    for w in int_words:
        cy = (w["top"] + w["bottom"]) / 2
        if any(
            w["x1"] < u["x0"] <= w["x1"] + 18
            and abs((u["top"] + u["bottom"]) / 2 - cy) <= 4
            and u["text"].strip(".")
            for u in words
        ):
            out.append(w)
        elif chars is not None and any(
            w["x1"] < c["x0"] <= w["x1"] + 6
            and abs((c["top"] + c["bottom"]) / 2 - cy) <= 4
            and c["text"] == " "
            for c in chars
        ):
            out.append(w)
    return out


def _find_legend_column(
    int_words: list[dict],
    words: list[dict],
    chars: list[dict] | None = None,
) -> dict[int, dict]:
    """Find the numbered legend: right-aligned integers forming 1..N.

    Returns map_number -> the number's word dict. The legend is a sequence of
    right-aligned (`x1`-aligned) columns: a first column 1..a, then continuation
    columns a+1.., etc. (large villages wrap into two or more columns). Starting
    from 1, we repeatedly take the column giving the longest contiguous run from
    the next needed number, then continue from where it ends. Only legend-like
    integers are considered, so a run of map labels or cross-reference numbers
    that happens to align can't masquerade as a continuation column.
    """
    groups: dict[float, list[dict]] = defaultdict(list)
    for w in _legend_like(int_words, words, chars):
        groups[round(w["x1"] / _X1_COL_TOL) * _X1_COL_TOL].append(w)
    by_num_per_group = {k: {int(w["text"]): w for w in ws} for k, ws in groups.items()}

    result: dict[int, dict] = {}
    used: set[float] = set()
    next_needed = 1
    while True:
        best_words: list[dict] = []
        best_key: float | None = None
        for key, by_num in by_num_per_group.items():
            if key in used:
                continue
            run = 0
            while (next_needed + run) in by_num:
                run += 1
            if run > len(best_words):
                best_words = [by_num[next_needed + i] for i in range(run)]
                best_key = key
        # The first column establishes 1..a; later columns must contribute a run
        # of at least two, so a lone stray legend-like integer can't tack a
        # phantom entry onto the end.
        if not best_words or (next_needed > 1 and len(best_words) < 2):
            break
        for w in best_words:
            result[int(w["text"])] = w
        used.add(best_key)
        next_needed += len(best_words)
    return result


def _legend_columns_x0(legend_col: dict[int, dict]) -> list[float]:
    """Left edge of each legend number column (a legend may wrap into several
    side-by-side columns). Number x0 varies a little within a column (1- vs
    2-digit width), so cluster nearby values and return one edge per column —
    otherwise that jitter creates false column boundaries that truncate names."""
    xs = sorted(round(w["x0"]) for w in legend_col.values())
    cols: list[float] = []
    for x in xs:
        if not cols or x - cols[-1] > 30:
            cols.append(x)
    return cols


def _is_junk_name(name: str) -> bool:
    """A primary name with no readable content: empty, or nothing but digits,
    spaces, dots and dashes — the debris a mis-parsed legend leaves behind
    (e.g. ".0 1 .- 2"). A real numeric address ("7 Manor Way") keeps its letters
    and so is not junk."""
    return not re.sub(r"[\s.\-\d]", "", name)


def _is_spaced_fragment(text: str) -> bool:
    """Single-character or short dot-bearing fragments from letter-spaced cross-reference
    text that bleeds into adjacent legend rows (e.g. '.C', 'r.', 'h.', 'c' from
    a letter-spaced 'Church Row' cross-reference entry)."""
    if len(text) > 3:
        return False
    return ("." in text and any(c.isalpha() for c in text)) or (len(text) == 1 and text.isalpha())


def _legend_row_words(w: dict, words: list[dict], col_x0s: list[float]) -> list[dict]:
    """Name words on a legend number's row: those to its right, stopping before
    the next legend column (wide legends pack several entries per row) and at any
    large gap (a merged-in map label)."""
    cy = (w["top"] + w["bottom"]) / 2
    right_bound = min([c for c in col_x0s if c > w["x1"] + 1], default=float("inf"))
    row = sorted(
        (u for u in words
         if abs((u["top"] + u["bottom"]) / 2 - cy) <= _ROW_BAND
         and w["x1"] - 0.5 < u["x0"] < right_bound - 2
         and u["text"].strip(".")
         and not _is_spaced_fragment(u["text"])),
        key=lambda u: u["x0"],
    )
    out: list[dict] = []
    prev_x1 = w["x1"]
    for u in row:
        if u["x0"] - prev_x1 > _NAME_MAX_GAP:
            break
        out.append(u)
        prev_x1 = u["x1"]
    return out


def _legend_names(words: list[dict], legend_col: dict[int, dict]) -> dict[int, str]:
    """The primary name for each legend number, read from its row's words so
    spacing is preserved (char-level reconstruction is only needed for the
    letter-spaced cross-reference)."""
    col_x0s = _legend_columns_x0(legend_col)
    names: dict[int, str] = {}
    for num, w in legend_col.items():
        toks = [u["text"] for u in _legend_row_words(w, words, col_x0s)]
        names[num] = re.sub(r"\s+", " ", " ".join(toks)).strip()
    return names


def _legend_row_bboxes(
    words: list[dict], legend_col: dict[int, dict]
) -> list[tuple[float, float, float, float]]:
    """Bounding box of each legend row (number + name), for excluding the
    leading digit of a numeric name (e.g. the "1" in "1 Eryholme Lane") from
    the map-label search."""
    col_x0s = _legend_columns_x0(legend_col)
    boxes: list[tuple[float, float, float, float]] = []
    for w in legend_col.values():
        toks = _legend_row_words(w, words, col_x0s)
        right = max([u["x1"] for u in toks] + [w["x1"]])
        boxes.append((w["x0"] - 1, w["top"] - 2, right + 1, w["bottom"] + 2))
    return boxes


def _parse_cross_reference(chars: list[dict]) -> dict[int, list[str]]:
    """Parse the alphabetical cross-reference for additional aliases.

    Lines carry dot leaders; the trailing right-aligned integer is the map
    number. Name chars sit in the right column (x0 >= name-column edge); stray
    map labels that merged in appear further left and are excluded by that bound.
    """
    xref: dict[int, list[str]] = defaultdict(list)
    dot_lines = [
        ln for ln in _cluster_by_top(chars)
        if sum(1 for c in ln if c["text"] == ".") >= _DOT_LEADER_MIN
    ]
    if not dot_lines:
        return xref
    # The name column's left edge: the dottiest sheets right-align names into a
    # column. Estimate it as the most common x0 of non-dot run-starts.
    name_left = _cross_ref_name_left(dot_lines)
    for ln in dot_lines:
        s = _reconstruct([c for c in ln if c["x0"] >= name_left])
        tokens = s.split()
        if tokens and _INT.match(tokens[-1]):
            num = int(tokens[-1])
            name = " ".join(tokens[:-1])
            if name and name not in xref[num]:
                xref[num].append(name)
    return xref


def _cross_ref_name_left(dot_lines: list[list[dict]]) -> float:
    """Left edge of the cross-reference name column (mode of line-start x0)."""
    starts: list[float] = []
    for ln in dot_lines:
        non_dot = [c for c in ln if c["text"] != "." and c["text"].strip()]
        if non_dot:
            starts.append(min(c["x0"] for c in non_dot))
    if not starts:
        return 0.0
    # Names start at a consistent x0; stray merged labels start further left, so
    # the median of per-line minimum-x0 lands on the name column for the common
    # case while staying robust to a few intruders.
    starts.sort()
    return starts[len(starts) // 2]


def _date_integer_ids(words: list[dict]) -> set[int]:
    """ids of integer words on the revision-date line ("9 January 2011").

    The drawing can extend to the top of the page, so map labels are not simply
    "anything high up". The one header integer that can collide with a legend
    number is the date's day. The date line is "DD Month YYYY": we require both a
    month name and a 4-digit year, so a house called "May House" (month word but
    no year) does not trip it.
    """
    ids: set[int] = set()
    for line in _cluster_by_top(words):
        texts = [w["text"].strip() for w in line]
        has_month = any(t.lower() in _MONTHS for t in texts)
        has_year = any(re.fullmatch(r"\d{4}", t) for t in texts)
        if has_month and has_year:
            ids.update(id(w) for w in line if _INT.match(w["text"].strip()))
    return ids


def _has_name_to_right(w: dict, words: list[dict]) -> bool:
    """True if a name word butts up to the right of integer `w` — i.e. `w` is the
    leading house number of an address like "7 Manor Way", not a standalone map
    label. Such a digit appears both in the legend and in the alphabetical
    cross-reference ("7 Manor Way .... 45"), and must never be taken as the map
    position of house number 7."""
    cy = (w["top"] + w["bottom"]) / 2
    for u in words:
        if not (w["x1"] < u["x0"] <= w["x1"] + 12):
            continue
        if abs((u["top"] + u["bottom"]) / 2 - cy) > 4:
            continue
        head = u["text"].strip(".")
        if head[:1].isalpha():
            return True
    return False


def _find_map_labels(
    chars: list[dict],
    words: list[dict],
    int_words: list[dict],
    legend_col: dict[int, dict],
    legend_bboxes: list[tuple[float, float, float, float]],
    dot_lines: list[list[dict]],
    date_ids: set[int],
) -> dict[int, PagePos]:
    """Isolate the scattered on-map number labels.

    An integer is excluded if it belongs to the numbered legend (its number or a
    leading digit of a numeric name) or is a cross-reference *number*. A
    cross-reference number is recognised locally and page-geometry-independently:
    it has dot-leader characters immediately to its left ("Name ...... 12"),
    whereas a map label printed on the drawing does not. When a number has
    several surviving candidates, the one not sharing a row with a
    cross-reference is the true drawing label and wins.
    """
    legend_ids = {id(w) for w in legend_col.values()}
    legend_nums = set(legend_col.keys())
    dots = [c for c in chars if c["text"] == "."]
    dot_bands = [
        (min(c["top"] for c in ln) - 2, max(c["bottom"] for c in ln) + 2) for ln in dot_lines
    ]

    def has_dot_to_left(w: dict) -> bool:
        cy = (w["top"] + w["bottom"]) / 2
        return any(
            abs((c["top"] + c["bottom"]) / 2 - cy) <= 4 and 0 <= w["x0"] - c["x1"] <= 8
            for c in dots
        )

    def on_dotline(w: dict) -> bool:
        cy = (w["top"] + w["bottom"]) / 2
        return any(t <= cy <= b for t, b in dot_bands)

    # Collect candidate labels, then prefer drawing-area (off-row) ones per number.
    best: dict[int, tuple[dict, bool]] = {}
    for w in int_words:
        if id(w) in legend_ids or id(w) in date_ids:
            continue
        num = int(w["text"])
        if num not in legend_nums:
            continue
        if has_dot_to_left(w):                   # cross-reference number
            continue
        cx = (w["x0"] + w["x1"]) / 2
        cy = (w["top"] + w["bottom"]) / 2
        if any(x0 <= cx <= x1 and t <= cy <= b for x0, t, x1, b in legend_bboxes):
            continue                             # leading digit of a numeric legend name
        if _has_name_to_right(w, words):         # leading digit of an address ("7 Manor Way")
            continue
        od = on_dotline(w)
        if num not in best or (best[num][1] and not od):
            best[num] = (w, od)

    return {
        num: PagePos(x=(w["x0"] + w["x1"]) / 2, y=(w["top"] + w["bottom"]) / 2)
        for num, (w, _) in best.items()
    }


def parse_sheet(pdf_path: Path, sheet_id: str) -> ParseResult:
    """Parse a single PDF: extract legend (with aliases) and map-label positions.
    Returns ParseResult with one ParsedHouse per legend entry."""
    notes: list[str] = []

    # Manual fixture for raster PDFs (e.g. Stapleton) — lives at
    # data/fixtures/<sheet_id>.json, two levels above the PDF cache dir.
    fixture_path = pdf_path.parent.parent.parent / "fixtures" / f"{sheet_id}.json"
    if fixture_path.exists():
        fixture = json.loads(fixture_path.read_text())
        houses = [
            ParsedHouse(
                map_number=h["map_number"],
                names=h["names"],
                names_normalized=[normalize_name(n) for n in h["names"]],
                page_pos=None,   # fixture houses carry no on-map position
            )
            for h in fixture
        ]
        return ParseResult(
            sheet_id=sheet_id,
            houses=houses,
            label_count_ok=True,
            notes=["Houses from manual fixture (raster PDF)"],
        )

    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        words = page.extract_words(keep_blank_chars=False, extra_attrs=["top", "bottom"])
        chars = list(page.chars)

    int_words = [w for w in words if _INT.match(w["text"].strip())]
    legend_col = _find_legend_column(int_words, words)

    # If the legend looks suspicious (many duplicate primary names, e.g. Eppleby where
    # the legend-index and address-number are printed with only a 2pt gap so extract_words
    # merges them into "11", "22" …), retry with x_tolerance=1 to force the split.
    def _names_suspicious(col: dict, ws: list[dict]) -> bool:
        primaries = [v for v in _legend_names(ws, col).values() if v]
        if len(primaries) < 2:
            # Entries found but all names empty after fragment-filtering means the
            # wrong column was chosen (cross-ref numbers whose adjacent text is all
            # letter-spaced fragments, as in Croft-on-Tees).
            return len(col) >= 2
        # Many duplicate primary names: legend-index and address-number concatenated
        # (Eppleby, East Layton: 2pt gap merges "6" + "22 South Parade" → "622…")
        if len(set(primaries)) / len(primaries) < 0.7:
            return True
        # Names contain letter-spaced cross-reference text: cross-ref page numbers
        # were mistaken for the legend column (Croft-on-Tees)
        spaced = sum(1 for n in primaries if n.count(".") > 1)
        return spaced / len(primaries) > 0.5

    # A legend gap smaller than the default extraction tolerance (~2-3pt, vs. Eppleby's
    # 1.7pt) can merge the number straight into the name — "1Spion Cop" — so the number
    # never appears as its own word at all (Angram, Ivelet: default pass finds 0-1
    # entries, not "many duplicates", so _names_suspicious alone never catches it). Retry
    # whenever the legend is missing or implausibly small, not just when names look wrong.
    if not legend_col or len(legend_col) < 2 or _names_suspicious(legend_col, words):
        tight_words = page.extract_words(
            keep_blank_chars=False, extra_attrs=["top", "bottom"], x_tolerance=1
        )
        tight_int = [w for w in tight_words if _INT.match(w["text"].strip())]
        tight_col = _find_legend_column(tight_int, tight_words, chars)
        # Only switch if the tighter pass actually found more — otherwise a real
        # 1-2 house legend would get discarded in favour of an equally-thin false start.
        if tight_col and not _names_suspicious(tight_col, tight_words) and len(tight_col) > len(legend_col):
            words, int_words, legend_col = tight_words, tight_int, tight_col

    if not legend_col:
        notes.append("No numbered legend column found")
        return ParseResult(sheet_id=sheet_id, houses=[], label_count_ok=False, notes=notes)

    names = {n: [v] for n, v in _legend_names(words, legend_col).items()}

    # Cross-reference: merge distinct aliases. Skip merge-garbage (dense or grid
    # cross-references reconstruct into runs of several entries); a real name has
    # at most one integer token and only as a leading house number.
    dot_lines = [
        ln for ln in _cluster_by_top(chars)
        if sum(1 for c in ln if c["text"] == ".") >= _DOT_LEADER_MIN
    ]
    xref = _parse_cross_reference(chars)
    for num, aliases in xref.items():
        if num not in names:
            continue
        for alias in aliases:
            tokens = alias.split()
            ints = [t for t in tokens if t.isdigit()]
            if len(ints) > 1 or (ints and not tokens[0].isdigit()) or len(alias) > 40:
                continue
            # Reject tokens that look like concatenated number+name ("59MoorRoad", "6ScotsDyke")
            if any(re.search(r"\d[A-Z][a-z]", t) for t in tokens):
                continue
            # A real alias is short ("Church", "St Mary's Church", "1 Eryholme Lane" — at
            # most 3 words). Longer ones are almost always two dot-leader lines merged by
            # tight row spacing (densely-set sheets like Carlton, Ivelet), producing
            # garbage like "2 Baygante Carlton Boarding Kennels" or "11 Thirley Cottage
            # Satron Farm". Dropping these loses a synonym at worst; keeping them shows
            # nonsense text next to a correct primary name.
            if len(tokens) > 3:
                continue
            if alias not in names[num]:
                names[num].append(alias)

    legend_bboxes = _legend_row_bboxes(words, legend_col)
    date_ids = _date_integer_ids(words)
    map_labels = _find_map_labels(
        chars, words, int_words, legend_col, legend_bboxes, dot_lines, date_ids
    )

    label_count_ok = set(map_labels.keys()) == set(legend_col.keys())
    if not label_count_ok:
        missing = set(legend_col.keys()) - set(map_labels.keys())
        extra = set(map_labels.keys()) - set(legend_col.keys())
        notes.append(
            f"Label/legend mismatch: legend={len(legend_col)}, labels={len(map_labels)}, "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )

    houses: list[ParsedHouse] = []
    for num in sorted(legend_col.keys()):
        house_names = names.get(num, [])
        houses.append(
            ParsedHouse(
                map_number=num,
                names=house_names,
                names_normalized=[normalize_name(n) for n in house_names],
                page_pos=map_labels.get(num),   # None when unnumbered on the drawing
            )
        )

    # Remove section-header entries: a bare street name like "Brompton Park" that
    # appears as the trailing component of 10+ other entries ("46 Brompton Park",
    # "131 Brompton Park"…) is a section label in the legend table, not a house.
    if len(houses) > 4:
        all_primaries = [h.names[0] for h in houses if h.names]
        remove = {
            h.map_number for h in houses
            if h.names and not h.names[0][:1].isdigit()
            and sum(1 for m in all_primaries if m.endswith(" " + h.names[0])) >= 10
        }
        if remove:
            houses = [h for h in houses if h.map_number not in remove]

    # Quality flag: a grid-style legend ("1 Forcett Close", "2 Forcett Close", …)
    # can be misread as a column of the address leading-digits, yielding many
    # houses that share one primary name. Surface it for review rather than ship
    # it silently.
    primary = [h.names[0] for h in houses if h.names and h.names[0]]
    if len(primary) >= 4 and len(set(primary)) / len(primary) < 0.7:
        notes.append(
            f"Suspicious legend: {len(houses)} houses but only "
            f"{len(set(primary))} distinct names (possible grid-layout misparse)"
        )

    # Quality flag: unreadable names. A legend template the parser can't handle
    # (e.g. the "glued number" layout on West Witton, where the map number is
    # printed hard against the name) leaves debris like ".0 1 .- 2" instead of a
    # name. Detect it so every scan surfaces the sheet for a manual fixture,
    # rather than silently shipping nonsense that reads as self-consistent.
    named = [h for h in houses if h.names]
    junk = [h for h in named if _is_junk_name(h.names[0])]
    if named and len(junk) / len(named) > 0.3:
        example = next((h.names[0] for h in junk if h.names[0]), "")
        notes.append(
            f"Parse likely failed: {len(junk)}/{len(named)} names are unreadable"
            + (f" (e.g. {example!r})" if example else "")
            + f". This sheet's legend template isn't supported — add a manual "
            f"fixture at data/fixtures/{sheet_id}.json (see stapleton.json / "
            f"west-witton.json for the format)."
        )

    return ParseResult(
        sheet_id=sheet_id,
        houses=houses,
        label_count_ok=label_count_ok,
        notes=notes,
    )
