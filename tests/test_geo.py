from math import isclose
import random
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from relief_uav.data import load_scenario
from relief_uav.data.models import Node
from relief_uav.geo import DigitalElevationModel, DemCoverageError, supercover_cells
from relief_uav.geo.segments import build_segment_matrix, dem_source_path


def _raster(tmp_path, elevations):
    path = tmp_path / "tiny.tif"
    with rasterio.open(path, "w", driver="GTiff", width=elevations.shape[1],
                       height=elevations.shape[0], count=1, dtype="float32",
                       crs="EPSG:4326", transform=from_origin(0, 3, 1, 1)) as ds:
        ds.write(elevations.astype("float32"), 1)
    return DigitalElevationModel(path)


def test_supercover_crosses_spike_cell_without_sampling(tmp_path):
    grid = np.zeros((3, 5), dtype=np.float32)
    grid[1, 2] = 999.0
    dem = _raster(tmp_path, grid)
    a = Node("A", "A", 0.2, 1.5, 0)
    b = Node("B", "B", 4.8, 1.5, 0)
    result = dem.analyze(a, b)
    assert (1, 2) in dem.segment_cells(a, b)
    assert result.maximum_ground_m == 999.0
    assert dem.segment_cells(a, b) == dem.segment_cells(b, a)
    assert result.distance_m > 0


def test_corner_and_gridline_touch_both_sides():
    cells = set(supercover_cells((0.5, 0.5), (2.5, 2.5), 3, 3))
    assert {(0, 0), (0, 1), (1, 0), (1, 1), (1, 2), (2, 1), (2, 2)} <= cells
    line = set(supercover_cells((1.0, 0.2), (1.0, 2.8), 3, 3))
    assert line == {(r, c) for r in range(3) for c in (0, 1)}


def _independent_rectangle_intersection(a, b, row, col):
    """Liang-Barsky clipping reference, independent of the grid-crossing method."""
    x0, y0 = a
    dx, dy = b[0] - x0, b[1] - y0
    lo, hi = 0.0, 1.0
    for p, q in ((-dx, x0 - col), (dx, col + 1 - x0),
                 (-dy, y0 - row), (dy, row + 1 - y0)):
        if p == 0:
            if q < 0:
                return False
        else:
            bound = q / p
            if p < 0:
                lo = max(lo, bound)
            else:
                hi = min(hi, bound)
    return lo <= hi + 1e-14


def test_supercover_matches_independent_rectangle_clipping():
    rng = random.Random(20260923)
    for _ in range(100):
        a = (rng.uniform(0.1, 5.9), rng.uniform(0.1, 5.9))
        b = (rng.uniform(0.1, 5.9), rng.uniform(0.1, 5.9))
        expected = {(r, c) for r in range(6) for c in range(6)
                    if _independent_rectangle_intersection(a, b, r, c)}
        assert set(supercover_cells(a, b, 6, 6)) == expected


def test_nodata_and_edges(tmp_path):
    grid = np.array([[1, -32767, 3], [4, 5, 6], [7, 8, 9]], dtype=np.float32)
    dem = _raster(tmp_path, grid)
    assert dem.metadata_nodata is None
    assert not dem.valid_cell(0, 1)
    assert dem.cell_center(0, 0) == (0.5, 2.5)
    a = Node("A", "A", 0.1, 2.5, 0)
    b = Node("B", "B", 2.9, 2.5, 0)
    assert dem.analyze(a, b).maximum_ground_m == 3
    with pytest.raises(DemCoverageError):
        dem.analyze(a, Node("OUT", "OUT", 3.1, 2.5, 0))


def test_real_directed_pairs_and_cache(tmp_path):
    scenario = load_scenario()
    cache = tmp_path / "pairs.json"
    first = build_segment_matrix(scenario, cache_path=cache, force_recompute=True)
    second = build_segment_matrix(scenario, cache_path=cache)
    assert cache.is_file() and not first.loaded_from_cache and second.loaded_from_cache
    assert len(first.records) == 16 * 16
    a = first.get("O01", "S001")
    reverse = first.get("S001", "O01")
    assert a.horizontal_distance_m == pytest.approx(reverse.horizontal_distance_m)
    assert a.maximum_dem_m == reverse.maximum_dem_m
    assert a.cruise_altitude_m == a.maximum_dem_m + 50
    assert a.start_operating_altitude_m == 127.7
    assert a.end_operating_altitude_m == 184
    assert reverse.start_operating_altitude_m == 184
    assert reverse.end_operating_altitude_m == 127.7
    assert a.climb_m == pytest.approx(reverse.descent_m)
    assert a.descent_m == pytest.approx(reverse.climb_m)
    assert second.get("O01", "S001") == a
    multi = first.get("S001", "S005")
    assert multi.start_operating_altitude_m == 184
    assert multi.end_operating_altitude_m == 195
    # Real metadata has no NoData tag, while PDF/MAT specifies -32767.
    dem = DigitalElevationModel(dem_source_path(scenario.root))
    assert dem.metadata_nodata is None
    assert dem.width == 1486 and dem.height == 1309
    assert all(dem.valid_cell(*dem.containing_cell(n.longitude_deg, n.latitude_deg))
               for n in scenario.nodes.values())
