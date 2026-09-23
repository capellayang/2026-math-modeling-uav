"""DEM cell-intersection line of sight; no sparse distance sampling."""

from math import floor

import numpy as np

from relief_uav.geo.dem import DigitalElevationModel, DemCoverageError
from .model import CommunicationEndpoint


def _cell_interval(x0: float, y0: float, x1: float, y1: float,
                   col: int, row: int) -> tuple[float, float] | None:
    """Closed-segment intersection with a closed raster cell (Liang-Barsky)."""
    lo, hi = 0.0, 1.0
    for origin, delta, lower, upper in (
        (x0, x1 - x0, col, col + 1), (y0, y1 - y0, row, row + 1)
    ):
        if abs(delta) < 1e-14:
            if origin < lower - 1e-10 or origin > upper + 1e-10:
                return None
        else:
            a, b = (lower - origin) / delta, (upper - origin) / delta
            lo, hi = max(lo, min(a, b)), min(hi, max(a, b))
            if lo > hi + 1e-12:
                return None
    return max(0.0, lo), min(1.0, hi)


def terrain_blocked(dem: DigitalElevationModel, a: CommunicationEndpoint,
                    b: CommunicationEndpoint) -> bool:
    """Conservative cell-height obstruction, including edge and corner touches.

    Each 30 m pixel is a constant-height terrain plate. A plate obstructs if
    its elevation reaches the 3-D sightline anywhere inside the plate. Invalid
    DEM cells on a LOS raise rather than being interpreted as clear terrain.
    """
    dem.containing_cell(a.longitude_deg, a.latitude_deg)
    dem.containing_cell(b.longitude_deg, b.latitude_deg)
    x0, y0 = dem._pixel_xy(a.longitude_deg, a.latitude_deg)
    x1, y1 = dem._pixel_xy(b.longitude_deg, b.latitude_deg)
    dx, dy = x1-x0, y1-y0
    crossing = [np.array([0.0, 1.0])]
    for start, delta in ((x0, dx), (y0, dy)):
        if abs(delta) > 1e-14:
            lines = np.arange(floor(min(start, start+delta)),
                              floor(max(start, start+delta))+2, dtype=float)
            ts = (lines-start)/delta
            crossing.append(ts[(ts > 0) & (ts < 1)])
    times = np.unique(np.concatenate(crossing))
    mid = (times[:-1]+times[1:])/2
    cols = np.floor(x0+dx*mid).astype(int)
    rows = np.floor(y0+dy*mid).astype(int)
    min_z = np.minimum(a.altitude_m+(b.altitude_m-a.altitude_m)*times[:-1],
                       a.altitude_m+(b.altitude_m-a.altitude_m)*times[1:])

    def check(rr, cc, zz):
        inside = (rr >= 0) & (rr < dem.height) & (cc >= 0) & (cc < dem.width)
        if not np.any(inside):
            return False
        rr, cc, zz = rr[inside], cc[inside], zz[inside]
        elevation = dem.elevations_m[rr, cc]
        valid = np.isfinite(elevation) & (elevation != dem.nodata_m)
        if dem.metadata_nodata is not None:
            valid &= elevation != dem.metadata_nodata
        if not np.all(valid):
            raise DemCoverageError("NoData on communication LOS")
        return bool(np.any(elevation >= zz-1e-9))

    if check(rows, cols, min_z):
        return True
    # Intersections at edges/corners can touch adjacent pixels for zero length;
    # include all such cells conservatively, including endpoints.
    xs, ys = x0+dx*times, y0+dy*times
    z = a.altitude_m+(b.altitude_m-a.altitude_m)*times
    base_c, base_r = np.floor(xs).astype(int), np.floor(ys).astype(int)
    on_x = np.isclose(xs, np.rint(xs), rtol=0, atol=1e-10)
    on_y = np.isclose(ys, np.rint(ys), rtol=0, atol=1e-10)
    for dc, dr in ((0, 0), (-1, 0), (0, -1), (-1, -1)):
        eligible = (on_x if dc else np.ones(len(times), dtype=bool)) & (
            on_y if dr else np.ones(len(times), dtype=bool))
        if check(base_r[eligible]+dr, base_c[eligible]+dc, z[eligible]):
            return True
    return False
