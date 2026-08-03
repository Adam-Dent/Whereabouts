"""The maths that turns a point on a drawing into a place on the earth.

Every coordinate in this dataset came through `fit_affine` and `apply_affine`.
If the fit is wrong, thousands of houses are wrong in a way that looks perfectly
plausible: the ring sits on a building, the app says "pinned exactly", and the
driver ends up in the wrong village. These are the tests that would notice.

The affine is built from control points placed by hand, so the tests below use
synthetic transforms with known answers rather than real sheets: a real sheet
can only tell you the fit is self-consistent, not that it is correct.
"""

from __future__ import annotations

import math

import pytest

from etl.models import ControlPoint, ControlPointsFile, LatLng, PagePos
from etl.transform import (
    RESIDUAL_THRESHOLD_M,
    apply_affine,
    coords_in_north_yorkshire,
    coords_within_radius,
    fit_affine,
)


def _cpf(pairs: list[tuple[tuple[float, float], tuple[float, float]]]) -> ControlPointsFile:
    return ControlPointsFile(
        sheet_id="test",
        pdf_hash="0" * 8,
        points=[
            ControlPoint(page=PagePos(x=px, y=py), world=LatLng(lat=lat, lng=lng))
            for (px, py), (lat, lng) in pairs
        ],
    )


# A deliberately simple ground truth: 1 page unit = 0.001 degrees, no rotation.
_TRUTH = [
    ((0.0, 0.0), (54.400, -1.800)),
    ((100.0, 0.0), (54.400, -1.700)),
    ((0.0, 100.0), (54.300, -1.800)),
    ((100.0, 100.0), (54.300, -1.700)),
]


def test_fit_recovers_an_exact_transform() -> None:
    affine, rms = fit_affine(_cpf(_TRUTH))
    assert rms == pytest.approx(0.0, abs=1e-6), "an exact fit must have no residual"
    for (px, py), (lat, lng) in _TRUTH:
        got_lng, got_lat = apply_affine(affine, PagePos(x=px, y=py))
        assert got_lat == pytest.approx(lat, abs=1e-9)
        assert got_lng == pytest.approx(lng, abs=1e-9)


def test_fit_interpolates_between_control_points() -> None:
    """The whole point: houses are not on the control points."""
    affine, _ = fit_affine(_cpf(_TRUTH))
    lng, lat = apply_affine(affine, PagePos(x=50.0, y=50.0))
    assert lat == pytest.approx(54.350, abs=1e-9)
    assert lng == pytest.approx(-1.750, abs=1e-9)


def test_fit_handles_a_rotated_sheet() -> None:
    """Colin's scans are not always square to the page."""
    theta = math.radians(7.0)
    pairs = []
    for px, py in [(0, 0), (100, 0), (0, 100), (100, 100), (50, 20)]:
        rx = px * math.cos(theta) - py * math.sin(theta)
        ry = px * math.sin(theta) + py * math.cos(theta)
        pairs.append(((float(px), float(py)), (54.4 - ry * 0.001, -1.8 + rx * 0.001)))
    affine, rms = fit_affine(_cpf(pairs))
    assert rms == pytest.approx(0.0, abs=1e-6)


def test_three_points_is_the_minimum_and_is_enough() -> None:
    affine, _ = fit_affine(_cpf(_TRUTH[:3]))
    lng, lat = apply_affine(affine, PagePos(x=100.0, y=100.0))
    assert lat == pytest.approx(54.300, abs=1e-9)
    assert lng == pytest.approx(-1.700, abs=1e-9)


def test_two_points_is_refused_rather_than_fitted() -> None:
    """An underdetermined fit would produce coordinates that look fine and are
    not, which is the worst possible failure for this project."""
    with pytest.raises(ValueError, match="at least 3"):
        fit_affine(_cpf(_TRUTH[:2]))


def test_a_mistyped_control_point_shows_up_as_residual() -> None:
    """The residual is the only signal that a control point was placed wrongly,
    and RESIDUAL_THRESHOLD_M is what turns it into a flag on the sheet."""
    bad = list(_TRUTH) + [((50.0, 50.0), (54.360, -1.750))]  # ~1.1km out
    _, rms = fit_affine(_cpf(bad))
    assert rms > RESIDUAL_THRESHOLD_M, "a badly placed control point must not pass silently"


def test_a_good_fit_stays_under_the_flagging_threshold() -> None:
    jittered = [
        ((px, py), (lat + 2e-6, lng - 2e-6)) for (px, py), (lat, lng) in _TRUTH
    ]
    _, rms = fit_affine(_cpf(jittered))
    assert rms < RESIDUAL_THRESHOLD_M


# ── County bounds ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("lat", "lng", "where"),
    [
        (54.4695, -1.8226, "Dalton, Richmondshire"),
        (53.6440, -1.0700, "Selby, the southern edge"),
        (54.2800, -0.4000, "the Scarborough coast"),
        (54.3100, -2.5000, "the western dales"),
    ],
)
def test_real_north_yorkshire_places_are_inside_the_bounds(lat: float, lng: float, where: str) -> None:
    """The bounds used to be drawn around Richmondshire and excluded both Selby
    and the coast, so valid coordinates were reported as out-of-county."""
    assert coords_in_north_yorkshire(lat, lng), where


@pytest.mark.parametrize(
    ("lat", "lng", "where"),
    [
        (55.9533, -3.1883, "Edinburgh"),
        (51.5074, -0.1278, "London"),
        (53.4808, -2.2426, "Manchester"),
        (-1.8226, 54.4695, "latitude and longitude transposed"),
    ],
)
def test_places_outside_the_county_are_rejected(lat: float, lng: float, where: str) -> None:
    assert not coords_in_north_yorkshire(lat, lng), where


# ── Distance from the village centre ─────────────────────────────────────────

def test_within_radius_measures_real_distance() -> None:
    # 0.01 degrees of latitude is about 1.113 km.
    assert coords_within_radius(54.41, -1.80, 54.40, -1.80, max_m=1200)
    assert not coords_within_radius(54.41, -1.80, 54.40, -1.80, max_m=1000)


def test_within_radius_accounts_for_longitude_converging() -> None:
    """A degree of longitude is shorter this far north, and treating it as
    equal to a degree of latitude would overstate east-west distances by
    about 40% at this latitude."""
    assert coords_within_radius(54.40, -1.7830, 54.40, -1.80, max_m=1200)


def test_a_house_at_its_own_centroid_is_within_any_radius() -> None:
    assert coords_within_radius(54.40, -1.80, 54.40, -1.80, max_m=1)
