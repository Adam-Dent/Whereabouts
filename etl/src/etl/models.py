"""Shared data model dataclasses matching the spec shapes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PagePos:
    x: float
    y: float


@dataclass
class ImagePos:
    x: float
    y: float


@dataclass
class ImageSize:
    w: int
    h: int


@dataclass
class LatLng:
    lat: float
    lng: float


@dataclass
class House:
    id: str                          # "<sheet_id>-<map_number>"
    village_id: str
    sheet_id: str
    map_number: int
    names: list[str]
    names_normalized: list[str]
    page_pos: Optional[PagePos]   # None when the house is not numbered on the drawing
    image_pos: Optional[ImagePos]
    lat: Optional[float]
    lng: Optional[float]
    source_pdf: str


@dataclass
class Sheet:
    id: str
    village_id: str
    village_name: str
    district: str
    pdf_url: str
    pdf_hash: str
    image_path: str
    image_size: ImageSize
    affine: Optional[list[float]]            # 6 numbers [a,b,c,d,e,f]
    georef_residual_m: Optional[float]
    control_point_count: int


@dataclass
class Village:
    id: str
    name: str
    district: str
    sheet_ids: list[str]
    centroid: Optional[LatLng]


@dataclass
class ControlPoint:
    page: PagePos
    world: LatLng


@dataclass
class ControlPointsFile:
    sheet_id: str
    pdf_hash: str
    points: list[ControlPoint] = field(default_factory=list)
