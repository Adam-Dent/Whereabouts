"""Affine georeferencing: fit control points, apply to house positions (spec §5.5, §6.4)."""

from __future__ import annotations

import math

import numpy as np

from .models import ControlPointsFile, PagePos


# North Yorkshire approximate bounding box for sanity checks
NY_BOUNDS = {
    "lat_min": 53.9,
    "lat_max": 54.7,
    "lng_min": -2.7,
    "lng_max": -0.5,
}

# Max allowed RMS residual (metres) before we flag the sheet
RESIDUAL_THRESHOLD_M = 25.0


def fit_affine(cpf: ControlPointsFile) -> tuple[list[float], float]:
    """
    Fit affine from page coords -> WGS84 using least squares.
    Returns (affine, rms_residual_m).
    affine = [a, b, c, d, e, f] where:
      lng = a*x + b*y + c
      lat = d*x + e*y + f
    """
    if len(cpf.points) < 3:
        raise ValueError(f"Need at least 3 control points, got {len(cpf.points)}")

    # Build the design matrix
    A = np.array(
        [[p.page.x, p.page.y, 1.0] for p in cpf.points],
        dtype=float,
    )
    lng_vec = np.array([p.world.lng for p in cpf.points], dtype=float)
    lat_vec = np.array([p.world.lat for p in cpf.points], dtype=float)

    (a, b, c), *_ = np.linalg.lstsq(A, lng_vec, rcond=None)
    (d, e, f), *_ = np.linalg.lstsq(A, lat_vec, rcond=None)

    affine = [float(a), float(b), float(c), float(d), float(e), float(f)]

    # Compute RMS residual in metres
    avg_lat = float(np.mean(lat_vec))
    metres_per_deg_lat = 111_320.0
    metres_per_deg_lng = 111_320.0 * math.cos(math.radians(avg_lat))

    residuals_m: list[float] = []
    for pt in cpf.points:
        pred_lng = a * pt.page.x + b * pt.page.y + c
        pred_lat = d * pt.page.x + e * pt.page.y + f
        dlng = (pred_lng - pt.world.lng) * metres_per_deg_lng
        dlat = (pred_lat - pt.world.lat) * metres_per_deg_lat
        residuals_m.append(math.sqrt(dlng**2 + dlat**2))

    rms = math.sqrt(sum(r**2 for r in residuals_m) / len(residuals_m))
    return affine, rms


def apply_affine(affine: list[float], page_pos: PagePos) -> tuple[float, float]:
    """Return (lng, lat) for a given page position."""
    a, b, c, d, e, f = affine
    lng = a * page_pos.x + b * page_pos.y + c
    lat = d * page_pos.x + e * page_pos.y + f
    return lng, lat


def coords_in_north_yorkshire(lat: float, lng: float) -> bool:
    return (
        NY_BOUNDS["lat_min"] <= lat <= NY_BOUNDS["lat_max"]
        and NY_BOUNDS["lng_min"] <= lng <= NY_BOUNDS["lng_max"]
    )


def coords_within_radius(
    lat: float,
    lng: float,
    centroid_lat: float,
    centroid_lng: float,
    max_m: float = 5000.0,
) -> bool:
    """Check house is within max_m metres of its village centroid."""
    metres_per_deg_lat = 111_320.0
    metres_per_deg_lng = 111_320.0 * math.cos(math.radians(centroid_lat))
    dlat = (lat - centroid_lat) * metres_per_deg_lat
    dlng = (lng - centroid_lng) * metres_per_deg_lng
    return math.sqrt(dlat**2 + dlng**2) <= max_m
