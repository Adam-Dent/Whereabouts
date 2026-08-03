"""Re-derive every village centroid, and check the ones we cannot re-derive.

The centroid is the directions fallback for every house not yet placed by hand,
so in a district at 0% placement it is the only answer a user ever gets. Eight
of them pointed at a different village of the same name, one by 98km, and the
only reason that was ever noticed is that those particular sheets happened to
have placements to contradict them.

This resolves the rest, in three passes:

1. Villages in a district that is already hand-placed take their centroid from
   the placed houses. That is ground truth and beats any lookup.
2. Villages whose name has exactly one match in North Yorkshire are
   unambiguous, so they take it. Their positions give each district a reference
   centre.
3. Villages whose name has several matches are resolved by picking the
   candidate nearest their district's reference centre. Colin's districts are
   geographically coherent, so "the Carlton in this district" is a question the
   candidate list can answer once you know where the district is.

Anything still unresolved is reported rather than guessed. Run with:

    uv run python -m etl.recentroid            # report only, no writes
    uv run python -m etl.recentroid --write    # update villages.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import median

from .geocode import _rough_metres, geocode_village, village_candidates

DATA = Path(__file__).parent.parent.parent.parent / "data"
DIST = DATA / "dist"
PLACEMENTS = DATA / "placements"

# A district's reference centre is only trustworthy once a few unambiguous
# villages agree on it; below this the "centre" is one village's opinion.
_MIN_ANCHORS = 3
# Past this from its district's centre, a resolved centroid is reported for a
# human to look at even though it was the nearest candidate. North Yorkshire
# districts are large, so this is deliberately generous.
_SUSPECT_M = 25_000.0


def _placed_centres() -> dict[str, tuple[float, float]]:
    """Village centre from hand-placed houses, keyed by village id."""
    houses = {h["id"]: h for h in json.loads((DIST / "houses.json").read_text())}
    sheets = {s["id"]: s for s in json.loads((DIST / "sheets.json").read_text())}
    pts: dict[str, list[tuple[float, float]]] = {}
    for f in sorted(PLACEMENTS.glob("*.json")):
        sheet = sheets.get(f.stem)
        if not sheet:
            continue
        vid = sheet["village_id"]
        for hid, p in json.loads(f.read_text()).get("houses", {}).items():
            if hid in houses and p.get("lat") is not None:
                pts.setdefault(vid, []).append((p["lat"], p["lng"]))
    return {
        vid: (round(median(p[0] for p in ps), 7), round(median(p[1] for p in ps), 7))
        for vid, ps in pts.items()
        if len(ps) >= 5
    }


def main() -> None:
    write = "--write" in sys.argv
    villages = json.loads((DIST / "villages.json").read_text())
    placed = _placed_centres()

    resolved: dict[str, tuple[float, float]] = {}
    source: dict[str, str] = {}
    ambiguous: list[dict] = []

    # Pass 1 and 2: placements, then unambiguous names.
    for v in villages:
        vid, name = v["id"], v["name"]
        if vid in placed:
            resolved[vid] = placed[vid]
            source[vid] = "placements"
            continue
        cands = village_candidates(name)
        if len(cands) == 1:
            resolved[vid] = cands[0]
            source[vid] = "unambiguous"
        elif not cands:
            source[vid] = "not found"
        else:
            ambiguous.append(v)

    # District reference centres, from everything settled so far.
    anchors: dict[str, list[tuple[float, float]]] = {}
    for v in villages:
        if v["id"] in resolved:
            anchors.setdefault(v["district"], []).append(resolved[v["id"]])
    centres = {
        d: (median(p[0] for p in ps), median(p[1] for p in ps))
        for d, ps in anchors.items()
        if len(ps) >= _MIN_ANCHORS
    }

    # Pass 3: resolve ambiguous names against their district's centre.
    suspect: list[str] = []
    for v in ambiguous:
        centre = centres.get(v["district"])
        pick = geocode_village(v["name"], near=centre) if centre else None
        if pick is None:
            source[v["id"]] = "ambiguous, unresolved"
            continue
        resolved[v["id"]] = pick
        source[v["id"]] = "disambiguated"
        if _rough_metres(pick, centre) > _SUSPECT_M:
            suspect.append(f"{v['name']} ({v['district']})")

    # What actually changed, and by how far.
    moved: list[tuple[float, str, str]] = []
    for v in villages:
        old, new = v.get("centroid"), resolved.get(v["id"])
        if new is None or not old:
            continue
        d = _rough_metres((old["lat"], old["lng"]), new)
        if d > 500:
            moved.append((d, v["name"], v["district"]))

    counts: dict[str, int] = {}
    for s in source.values():
        counts[s] = counts.get(s, 0) + 1
    print(f"{len(villages)} villages")
    for k in sorted(counts):
        print(f"  {counts[k]:4d}  {k}")

    moved.sort(reverse=True)
    print(f"\n{len(moved)} centroids move by more than 500m:")
    for d, name, district in moved[:40]:
        print(f"  {d / 1000:8.1f} km  {name} ({district})")

    if suspect:
        print(f"\n{len(suspect)} resolved but far from their district, worth an eye:")
        for s in suspect:
            print(f"  {s}")

    unresolved = [v["name"] for v in villages if v["id"] not in resolved]
    print(f"\n{len(unresolved)} villages have no centroid: {', '.join(unresolved[:20])}")

    if not write:
        print("\nReport only. Re-run with --write to update villages.json.")
        return

    for v in villages:
        new = resolved.get(v["id"])
        v["centroid"] = {"lat": new[0], "lng": new[1]} if new else None
    (DIST / "villages.json").write_text(json.dumps(villages, indent=2))
    print("\nvillages.json updated. Rebuild the PWA to ship it.")


if __name__ == "__main__":
    main()
