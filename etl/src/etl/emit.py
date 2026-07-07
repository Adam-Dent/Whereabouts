"""Assemble and write houses.json, villages.json, report.md (spec §5.5, §5.6)."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import asdict
from pathlib import Path

from .models import House, Sheet, Village
from .transform import RESIDUAL_THRESHOLD_M


def _serialise(obj: object) -> object:
    """Custom JSON-serialisable converter for dataclasses."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)  # type: ignore[arg-type]
    raise TypeError(f"Not serialisable: {type(obj)}")


def emit_dataset(
    houses: list[House],
    sheets: list[Sheet],
    villages: list[Village],
    dist_dir: Path,
    report_lines: list[str],
) -> None:
    """Write houses.json, sheets.json, villages.json and report.md."""
    dist_dir.mkdir(parents=True, exist_ok=True)

    (dist_dir / "houses.json").write_text(
        json.dumps([asdict(h) for h in houses], indent=2)
    )
    (dist_dir / "sheets.json").write_text(
        json.dumps([asdict(s) for s in sheets], indent=2)
    )
    (dist_dir / "villages.json").write_text(
        json.dumps([asdict(v) for v in villages], indent=2)
    )

    report = _build_report(houses, sheets, report_lines)
    (dist_dir / "report.md").write_text(report)
    print(f"Emitted dataset to {dist_dir}")


def _build_report(
    houses: list[House],
    sheets: list[Sheet],
    extra_lines: list[str],
) -> str:
    lines = ["# Whereabouts ETL Report\n"]

    georeffed = [s for s in sheets if s.affine is not None]
    not_georeffed = [s for s in sheets if s.affine is None]
    lines.append(f"**Sheets parsed:** {len(sheets)}")
    lines.append(f"**Georeferenced:** {len(georeffed)}")
    lines.append(f"**Awaiting georeferencing:** {len(not_georeffed)}")
    lines.append("")

    high_residual = [
        s for s in georeffed
        if s.georef_residual_m and s.georef_residual_m > RESIDUAL_THRESHOLD_M
    ]
    if high_residual:
        lines.append(f"## High residual (>{RESIDUAL_THRESHOLD_M} m RMS)\n")
        for s in high_residual:
            lines.append(f"- {s.id}: {s.georef_residual_m:.1f} m")
        lines.append("")

    if not_georeffed:
        lines.append("## Not yet georeferenced\n")
        for s in not_georeffed:
            lines.append(f"- {s.id} ({s.village_name})")
        lines.append("")

    if extra_lines:
        lines.append("## Parsing notes\n")
        lines.extend(extra_lines)

    return "\n".join(lines) + "\n"
