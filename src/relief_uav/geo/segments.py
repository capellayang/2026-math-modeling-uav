"""Directed node-pair geometry and reusable, source-fingerprinted JSON cache."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from relief_uav.data.models import Node, Scenario
from .dem import DigitalElevationModel

CACHE_VERSION = 1
DEFAULT_CACHE = Path("outputs/cache/node_pair_geometry.json")


@dataclass(frozen=True)
class DirectedSegment:
    start_id: str
    end_id: str
    horizontal_distance_m: float
    maximum_dem_m: float
    cruise_altitude_m: float
    start_operating_altitude_m: float
    end_operating_altitude_m: float
    climb_m: float
    descent_m: float
    touched_cell_count: int


@dataclass(frozen=True)
class SegmentMatrix:
    records: dict[str, DirectedSegment]
    cache_path: Path
    loaded_from_cache: bool
    source_fingerprint: str

    @staticmethod
    def key(start_id: str, end_id: str) -> str:
        return f"{start_id}->{end_id}"

    def get(self, start_id: str, end_id: str) -> DirectedSegment:
        return self.records[self.key(start_id, end_id)]


def dem_source_path(root: Path) -> Path:
    return root / "数据/镇龙乡地理空间数据/镇龙乡及周边地理数据/数字高程模型数据（DEM）/镇龙乡及周边30米DEM.tif"


def _fingerprint(nodes: dict[str, Node], dem_path: Path) -> str:
    h = sha256()
    h.update(str(CACHE_VERSION).encode())
    h.update(json.dumps([asdict(nodes[k]) for k in sorted(nodes)], ensure_ascii=False, sort_keys=True).encode())
    with dem_path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _record(start: Node, end: Node, dem: DigitalElevationModel) -> DirectedSegment:
    segment = dem.analyze(start, end)
    cruise = segment.maximum_ground_m + 50.0
    start_alt = start.operating_altitude_m
    end_alt = end.operating_altitude_m
    climb, descent = cruise - start_alt, cruise - end_alt
    if climb < -1e-9 or descent < -1e-9:
        raise ValueError(f"Cruise altitude below node operating altitude: {start.node_id}->{end.node_id}")
    return DirectedSegment(start.node_id, end.node_id, segment.distance_m,
                           segment.maximum_ground_m, cruise, start_alt, end_alt,
                           climb, descent, segment.touched_cell_count)


def build_segment_matrix(
    scenario: Scenario, *, cache_path: Path | None = None, force_recompute: bool = False,
) -> SegmentMatrix:
    nodes = scenario.nodes
    dem_path = dem_source_path(scenario.root)
    cache_path = Path(cache_path or scenario.root / DEFAULT_CACHE).resolve()
    fingerprint = _fingerprint(nodes, dem_path)
    if cache_path.is_file() and not force_recompute:
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            if payload["version"] == CACHE_VERSION and payload["source_fingerprint"] == fingerprint:
                records = {k: DirectedSegment(**v) for k, v in payload["records"].items()}
                if len(records) == len(nodes) ** 2 and all(
                    SegmentMatrix.key(i, j) in records for i in nodes for j in nodes
                ):
                    return SegmentMatrix(records, cache_path, True, fingerprint)
        except (ValueError, KeyError, TypeError):
            pass
    dem = DigitalElevationModel(dem_path)
    records = {SegmentMatrix.key(i, j): _record(nodes[i], nodes[j], dem)
               for i in sorted(nodes) for j in sorted(nodes)}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": CACHE_VERSION, "source_fingerprint": fingerprint,
               "method": "WGS84 geodesic length; EPSG:4326 affine raster supercover",
               "records": {k: asdict(v) for k, v in records.items()}}
    with NamedTemporaryFile("w", dir=cache_path.parent, prefix=".geometry_", suffix=".json",
                            encoding="utf-8", delete=False) as stream:
        temp_path = Path(stream.name)
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    os.replace(temp_path, cache_path)
    return SegmentMatrix(records, cache_path, False, fingerprint)
