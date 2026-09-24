"""Compare scalar and diversity-preserving Q3 route archives without publication."""

from dataclasses import asdict, replace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
sys.path.insert(0, str(ROOT/"scripts"))

from q3_q4_improvement import OUT, save_json, sheet
from relief_uav.communication.coverage import RadioEnvironment
from relief_uav.data import load_scenario
from relief_uav.geo import build_segment_matrix
from relief_uav.geo.dem import DigitalElevationModel
from relief_uav.geo.segments import dem_source_path
from relief_uav.q2.report import load_q2_solution
from relief_uav.q3.model import Q3AlgorithmConfig, RelayHoverPoint
from relief_uav.q3.search import search_routes_q3


def main():
    scenario = load_scenario(ROOT)
    segments = build_segment_matrix(scenario)
    env = RadioEnvironment(scenario, DigitalElevationModel(dem_source_path(ROOT)))
    baseline = load_q2_solution(ROOT/"outputs/q3/baseline_q2_v2_summary.json")
    rows = []
    for mode in ("scalar", "multiobjective"):
        config = replace(Q3AlgorithmConfig(), route_archive_mode=mode,
                         iterations=1200, restarts=6)
        found = search_routes_q3(env, segments, baseline, config, budget_s=90)
        archive = found.candidates
        row = (mode, found.iterations, found.elapsed_s, len(archive),
               len({c.demand_pattern for c in archive}),
               len({str(c.specs) for c in archive}),
               min(c.fast_transport.objective.weighted_tardiness for c in archive),
               min(c.fast_transport.objective.makespan_s for c in archive),
               min(c.direct_blackout_s for c in archive))
        rows.append(row)
        save_json(OUT/f"route_archive_{mode}.json", {
            "method": mode, "summary": row,
            "candidates": [{"specs": [asdict(s) for s in c.specs],
                            "demand_pattern": c.demand_pattern,
                            "j1": c.fast_transport.objective.weighted_tardiness,
                            "j2": c.fast_transport.objective.makespan_s,
                            "energy_kwh": c.fast_transport.objective.total_energy_kwh,
                            "transport_sorties": c.fast_transport.objective.sortie_count,
                            "blackout_s": c.direct_blackout_s,
                            "score": c.score}
                           for c in archive]})
        print(mode, row, flush=True)
    sheet("q3_route_archive_comparison.xlsx",
          ["mode", "iterations", "elapsed_s", "archive_count",
           "distinct_demand_patterns", "distinct_route_sets", "minimum_J1",
           "minimum_transport_J2_s", "minimum_direct_blackout_s"], rows)


if __name__ == "__main__":
    main()
