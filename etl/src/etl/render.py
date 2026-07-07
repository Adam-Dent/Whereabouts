"""PDF to PNG rendering via PyMuPDF (spec §5.3)."""

from __future__ import annotations

from pathlib import Path

import fitz  # pymupdf


DPI = 200
PDF_POINTS_PER_INCH = 72.0
SCALE = DPI / PDF_POINTS_PER_INCH   # pixels per PDF point


def render_sheet(pdf_path: Path, out_path: Path) -> tuple[int, int, float]:
    """
    Render the first page of pdf_path to out_path as PNG at DPI dpi.
    Returns (width_px, height_px, scale) where scale is pixels-per-PDF-point.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    page = doc[0]
    mat = fitz.Matrix(SCALE, SCALE)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    pix.save(str(out_path))
    doc.close()
    return pix.width, pix.height, SCALE


def page_pos_to_image_pos(
    page_x: float,
    page_y: float,
    scale: float,
) -> tuple[float, float]:
    """Convert pdfplumber page coordinates to pixel coordinates in the rendered PNG."""
    return page_x * scale, page_y * scale
