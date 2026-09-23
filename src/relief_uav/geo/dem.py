"""GeoTIFF DEM and exact raster-cell supercover for a straight lon/lat segment.

The source CRS is EPSG:4326. A route is defined as the straight segment between
its endpoint coordinates in that CRS. Pixel intersections are solved analytically
in affine pixel coordinates; no fixed-distance sampling is used. Horizontal
length is independently measured in metres on WGS84 with pyproj.Geod.
"""

from dataclasses import dataclass
from math import floor, isclose, isfinite
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Geod
from rasterio.transform import rowcol

from relief_uav.data.models import Node

_WGS84 = Geod(ellps="WGS84")
ATTACHMENT_NODATA = -32767.0


class DemCoverageError(ValueError):
    """A route leaves the DEM or has no usable elevation cells."""


@dataclass(frozen=True)
class DemSegment:
    distance_m: float
    maximum_ground_m: float
    touched_cell_count: int


def horizontal_distance_m(start: Node, end: Node) -> float:
    """WGS84 ellipsoidal inverse distance in metres, not a degree difference."""
    _, _, distance = _WGS84.inv(start.longitude_deg, start.latitude_deg,
                               end.longitude_deg, end.latitude_deg)
    return float(distance)


def _touching_indices(coord: float, size: int) -> tuple[int, ...]:
    """Every in-bounds cell whose closed interval contains this coordinate."""
    nearest = round(coord)
    if isclose(coord, nearest, rel_tol=0.0, abs_tol=1e-10):
        candidates = (nearest - 1, nearest)
    else:
        candidates = (floor(coord),)
    return tuple(i for i in candidates if 0 <= i < size)


def supercover_cells(
    start_xy: tuple[float, float], end_xy: tuple[float, float],
    width: int, height: int,
) -> tuple[tuple[int, int], ...]:
    """Return all cells touched by the closed segment, including corner ties.

    Coordinates are continuous pixel coordinates (x=column, y=row). All grid
    boundary crossing parameters are determined exactly from the line equation;
    interval midpoints cover interiors and crossing points cover edges/corners.
    A segment exactly on a grid line includes cells on both sides.
    """
    x0, y0 = start_xy
    x1, y1 = end_xy
    if not all(isfinite(v) for v in (x0, y0, x1, y1)):
        raise ValueError("Pixel coordinates must be finite")
    times = [0.0, 1.0]
    for a, b in ((x0, x1), (y0, y1)):
        delta = b - a
        if delta == 0:
            continue
        for grid_line in range(floor(min(a, b)), floor(max(a, b)) + 2):
            t = (grid_line - a) / delta
            if 0 < t < 1:
                times.append(t)
    times.sort()
    unique_times = []
    for t in times:
        if not unique_times or not isclose(t, unique_times[-1], rel_tol=0.0, abs_tol=1e-12):
            unique_times.append(t)
    cells = set()

    def add(t: float) -> None:
        x = x0 + (x1 - x0) * t
        y = y0 + (y1 - y0) * t
        for row in _touching_indices(y, height):
            for col in _touching_indices(x, width):
                cells.add((row, col))

    for index, t in enumerate(unique_times):
        add(t)
        if index + 1 < len(unique_times):
            add((t + unique_times[index + 1]) / 2)
    return tuple(sorted(cells))


class DigitalElevationModel:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        with rasterio.open(self.path) as ds:
            if ds.count != 1 or str(ds.crs) != "EPSG:4326":
                raise ValueError("Expected one-band EPSG:4326 GeoTIFF")
            self.transform = ds.transform
            self.width = ds.width
            self.height = ds.height
            self.bounds = ds.bounds
            self.metadata_nodata = ds.nodata
            self.elevations_m = ds.read(1)
        self.nodata_m = ATTACHMENT_NODATA

    def _pixel_xy(self, longitude_deg: float, latitude_deg: float) -> tuple[float, float]:
        col, row = (~self.transform) @ (longitude_deg, latitude_deg)
        return float(col), float(row)

    def containing_cell(self, longitude_deg: float, latitude_deg: float) -> tuple[int, int]:
        if not (self.bounds.left <= longitude_deg <= self.bounds.right and
                self.bounds.bottom <= latitude_deg <= self.bounds.top):
            raise DemCoverageError("Point is outside DEM bounds")
        row, col = rowcol(self.transform, longitude_deg, latitude_deg)
        if not (0 <= row < self.height and 0 <= col < self.width):
            raise DemCoverageError("Point is on an unrepresented outer edge")
        return int(row), int(col)

    def cell_center(self, row: int, col: int) -> tuple[float, float]:
        if not (0 <= row < self.height and 0 <= col < self.width):
            raise IndexError((row, col))
        return rasterio.transform.xy(self.transform, row, col, offset="center")

    def valid_cell(self, row: int, col: int) -> bool:
        value = float(self.elevations_m[row, col])
        return isfinite(value) and value != ATTACHMENT_NODATA and (
            self.metadata_nodata is None or value != self.metadata_nodata
        )

    def segment_cells(self, start: Node, end: Node) -> tuple[tuple[int, int], ...]:
        # Both endpoints must be inside the source DEM even if line touches edge.
        self.containing_cell(start.longitude_deg, start.latitude_deg)
        self.containing_cell(end.longitude_deg, end.latitude_deg)
        return supercover_cells(
            self._pixel_xy(start.longitude_deg, start.latitude_deg),
            self._pixel_xy(end.longitude_deg, end.latitude_deg),
            self.width, self.height,
        )

    def analyze(self, start: Node, end: Node) -> DemSegment:
        cells = self.segment_cells(start, end)
        values = [float(self.elevations_m[row, col]) for row, col in cells if self.valid_cell(row, col)]
        if not values:
            raise DemCoverageError("No valid DEM cells intersect the segment")
        return DemSegment(horizontal_distance_m(start, end), max(values), len(cells))
