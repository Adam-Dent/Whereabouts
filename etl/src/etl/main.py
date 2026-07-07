"""ETL entry point: discover -> parse -> render -> emit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .coverage import build_coverage_report
from .discover import discover_sheets, download_pdf, DISTRICT_INDEXES
from .emit import emit_dataset
from .geocode import geocode_village
from .models import House, ImagePos, PagePos, Sheet, Village, LatLng, ImageSize
from .parse import parse_sheet
from .render import render_sheet, page_pos_to_image_pos
from .slugs import house_id
from .transform import fit_affine, apply_affine, coords_in_north_yorkshire, coords_within_radius

DATA_DIR = Path(__file__).parent.parent.parent.parent / "data"


def _sheet_from_dict(d: dict) -> Sheet:
    return Sheet(
        id=d["id"], village_id=d["village_id"], village_name=d["village_name"],
        district=d["district"], pdf_url=d["pdf_url"], pdf_hash=d["pdf_hash"],
        image_path=d["image_path"], image_size=ImageSize(**d["image_size"]),
        affine=d.get("affine"), georef_residual_m=d.get("georef_residual_m"),
        control_point_count=d.get("control_point_count", 0),
    )


def _house_from_dict(d: dict) -> House:
    return House(
        id=d["id"], village_id=d["village_id"], sheet_id=d["sheet_id"],
        map_number=d["map_number"], names=d["names"], names_normalized=d["names_normalized"],
        page_pos=PagePos(**d["page_pos"]) if d.get("page_pos") else None,
        image_pos=ImagePos(**d["image_pos"]) if d.get("image_pos") else None,
        lat=d.get("lat"), lng=d.get("lng"), source_pdf=d["source_pdf"],
    )


def _load_existing(dist_dir: Path, processed_sheet_ids: set[str]) -> tuple[list[Sheet], list[House]]:
    """Sheets/houses from the last emitted dataset that this run didn't touch.

    A scoped run (--district / --sheet) only processes some sheets; without this,
    emit_dataset would overwrite houses.json with just the scoped subset and silently
    drop every other district's already-placed data.
    """
    sheets_path = dist_dir / "sheets.json"
    houses_path = dist_dir / "houses.json"
    kept_sheets = [
        _sheet_from_dict(d) for d in json.loads(sheets_path.read_text())
        if d["id"] not in processed_sheet_ids
    ] if sheets_path.exists() else []
    kept_houses = [
        _house_from_dict(d) for d in json.loads(houses_path.read_text())
        if d["sheet_id"] not in processed_sheet_ids
    ] if houses_path.exists() else []
    return kept_sheets, kept_houses


def run_pipeline(
    data_dir: Path,
    district_indexes: dict[str, str] | None = None,
    single_sheet: str | None = None,
) -> None:
    dist_dir = data_dir / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    (dist_dir / "images").mkdir(exist_ok=True)
    cp_dir = data_dir / "control_points"
    cache_dir = data_dir / ".cache" / "pdfs"

    # ── 1. Discover ──────────────────────────────────────────────────────────
    print("\n=== DISCOVER ===")
    scoped_districts = set((district_indexes or DISTRICT_INDEXES).keys())
    existing_ids: set[str] = set()
    sheets_path = dist_dir / "sheets.json"
    if sheets_path.exists():
        existing_ids = {
            d["id"] for d in json.loads(sheets_path.read_text())
            if d["district"] not in scoped_districts
        }
    sheets = discover_sheets(district_indexes, existing_ids=existing_ids)
    if single_sheet:
        sheets = [s for s in sheets if s.id == single_sheet]
        if not sheets:
            print(f"Sheet '{single_sheet}' not found in discovery. Aborting.")
            sys.exit(1)

    # ── 2. Download PDFs ─────────────────────────────────────────────────────
    print("\n=== DOWNLOAD ===")
    for sheet in sheets:
        cached = cache_dir / f"{sheet.id}.pdf"
        if cached.exists():
            import hashlib
            h = hashlib.sha256(cached.read_bytes()).hexdigest()
            sheet.pdf_hash = h
            print(f"  {sheet.id}: cached ({h[:8]}…)")
        else:
            print(f"  {sheet.id}: downloading {sheet.pdf_url}")
            try:
                _, h = download_pdf(sheet, cache_dir)
                sheet.pdf_hash = h
            except Exception as e:
                print(f"    WARN: {e}")

    # ── 3. Parse ─────────────────────────────────────────────────────────────
    print("\n=== PARSE ===")
    all_houses: list[House] = []
    parse_notes: list[str] = []
    for sheet in sheets:
        pdf_path = cache_dir / f"{sheet.id}.pdf"
        if not pdf_path.exists():
            print(f"  {sheet.id}: no PDF, skipping")
            continue
        result = parse_sheet(pdf_path, sheet.id)
        status = "OK" if result.label_count_ok else "MISMATCH"
        print(f"  {sheet.id}: {len(result.houses)} houses [{status}]")
        if result.notes:
            for note in result.notes:
                print(f"    ! {note}")
            parse_notes.extend([f"{sheet.id}: {n}" for n in result.notes])

        # ── 4. Render ─────────────────────────────────────────────────────
        out_png = dist_dir / "images" / f"{sheet.id}.png"
        w, h_px, scale = render_sheet(pdf_path, out_png)
        sheet.image_size = ImageSize(w=w, h=h_px)

        # ── 5. Apply affine (if control points exist) ──────────────────────
        affine = None
        centroid = None
        cp_file = cp_dir / f"{sheet.id}.json"
        if cp_file.exists():
            raw_cp = json.loads(cp_file.read_text())
            if raw_cp.get("points") and raw_cp.get("pdf_hash") == sheet.pdf_hash:
                from .models import ControlPointsFile, ControlPoint, PagePos, LatLng as LLng
                cpf = ControlPointsFile(
                    sheet_id=sheet.id,
                    pdf_hash=raw_cp["pdf_hash"],
                    points=[
                        ControlPoint(
                            page=PagePos(**p["page"]),
                            world=LLng(**p["world"]),
                        )
                        for p in raw_cp["points"]
                    ],
                )
                try:
                    affine, rms = fit_affine(cpf)
                    sheet.affine = affine
                    sheet.georef_residual_m = rms
                    sheet.control_point_count = len(cpf.points)
                    print(f"    georef: {rms:.1f} m RMS from {len(cpf.points)} points")
                except ValueError as e:
                    print(f"    WARN georef: {e}")
            elif raw_cp.get("points") and raw_cp.get("pdf_hash") != sheet.pdf_hash:
                parse_notes.append(
                    f"{sheet.id}: stale control points (hash mismatch), re-georeference needed"
                )

        # ── 6. Build House objects ─────────────────────────────────────────
        geo_ok = geocode_village(sheet.village_name)
        if geo_ok:
            centroid = LatLng(lat=geo_ok[0], lng=geo_ok[1])

        outlier_count = 0
        for ph in result.houses:
            # page_pos is None when the house has no number printed on the
            # drawing: then there is no on-map position to derive.
            image_pos = None
            if ph.page_pos is not None:
                ix, iy = page_pos_to_image_pos(ph.page_pos.x, ph.page_pos.y, scale)
                image_pos = ImagePos(x=ix, y=iy)
            lat_val = None
            lng_val = None
            if affine and ph.page_pos is not None:
                lng_c, lat_c = apply_affine(affine, ph.page_pos)
                ok = coords_in_north_yorkshire(lat_c, lng_c)
                if ok and centroid:
                    ok = coords_within_radius(lat_c, lng_c, centroid.lat, centroid.lng)
                if ok:
                    lat_val = lat_c
                    lng_val = lng_c
                else:
                    outlier_count += 1
                    parse_notes.append(
                        f"{sheet.id}/{house_id(sheet.id, ph.map_number)}: coordinate outlier excluded"
                    )
            all_houses.append(House(
                id=house_id(sheet.id, ph.map_number),
                village_id=sheet.village_id,
                sheet_id=sheet.id,
                map_number=ph.map_number,
                names=ph.names,
                names_normalized=ph.names_normalized,
                page_pos=ph.page_pos,
                image_pos=image_pos,
                lat=lat_val,
                lng=lng_val,
                source_pdf=sheet.pdf_url,
            ))
        if outlier_count:
            print(f"    {outlier_count} outlier(s) excluded")

    # ── 7. Merge with previously emitted sheets/houses outside this run's scope ─
    processed_sheet_ids = {s.id for s in sheets}
    kept_sheets, kept_houses = _load_existing(dist_dir, processed_sheet_ids)
    if kept_sheets:
        print(f"\nKeeping {len(kept_sheets)} previously-emitted sheet(s) outside this run's scope")
    sheets = kept_sheets + sheets
    all_houses = kept_houses + all_houses

    # ── 8. Build Villages ────────────────────────────────────────────────────
    print("\n=== VILLAGES ===")
    village_map: dict[str, dict] = {}
    for sheet in sheets:
        vid = sheet.village_id
        if vid not in village_map:
            geo = geocode_village(sheet.village_name)
            centroid = {"lat": geo[0], "lng": geo[1]} if geo else None
            village_map[vid] = {
                "id": vid,
                "name": sheet.village_name,
                "district": sheet.district,
                "sheet_ids": [],
                "centroid": centroid,
            }
            print(f"  {vid}: centroid={centroid}")
        village_map[vid]["sheet_ids"].append(sheet.id)

    villages = [Village(
        id=v["id"], name=v["name"], district=v["district"],
        sheet_ids=v["sheet_ids"],
        centroid=LatLng(**v["centroid"]) if v["centroid"] else None,
    ) for v in village_map.values()]

    # ── 9. Emit ──────────────────────────────────────────────────────────────
    print("\n=== EMIT ===")
    emit_dataset(all_houses, sheets, villages, dist_dir, parse_notes)

    # ── 10. Coverage report ─────────────────────────────────────────────────
    # Always measured against the full DISTRICT_INDEXES registry, not just the
    # districts processed this run, so it tracks what's left as well as what's done.
    print("\n=== COVERAGE ===")
    report = build_coverage_report(dist_dir, data_dir / "placements")
    (dist_dir / "coverage.md").write_text(report)
    print(f"Coverage report written to {dist_dir / 'coverage.md'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Whereabouts ETL pipeline")
    parser.add_argument("--sheet", default=None, help="Process a single sheet by ID")
    parser.add_argument(
        "--district",
        action="append",
        default=None,
        help="Limit the run to one district (repeatable for several). "
             "Default: every district in DISTRICT_INDEXES. "
             f"Known districts: {', '.join(DISTRICT_INDEXES)}",
    )
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args()

    district_indexes = None
    if args.district:
        unknown = [d for d in args.district if d not in DISTRICT_INDEXES]
        if unknown:
            print(f"Unknown district(s): {unknown}. Known: {list(DISTRICT_INDEXES)}")
            sys.exit(1)
        district_indexes = {d: DISTRICT_INDEXES[d] for d in args.district}

    run_pipeline(args.data_dir, district_indexes=district_indexes, single_sheet=args.sheet)


if __name__ == "__main__":
    main()
