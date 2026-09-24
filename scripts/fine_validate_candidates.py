"""Independent 0.25 s Q3 check for representative experimental snapshots."""

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
from relief_uav.q3.report import load_q3_solution
from relief_uav.validation.q3 import validate_q3


def main():
    scenario = load_scenario(ROOT)
    segments = build_segment_matrix(scenario)
    env = RadioEnvironment(scenario, DigitalElevationModel(dem_source_path(ROOT)))
    names = ("COMPONENT_REUSE_8", "MULTIOBJECTIVE_ENERGY",
             "ROUTE_MULTIOBJECTIVE_03")
    rows = []
    for name in names:
        path = OUT/f"{name.lower()}_q3.json"
        if not path.is_file():
            continue
        q3 = load_q3_solution(path)
        result = validate_q3(env, segments, q3, max_step_s=.25)
        row = (name, "PASS" if result.passed else "FAIL", result.outage_s,
               result.minimum_relay_access_margin_db,
               result.minimum_relay_backhaul_margin_db,
               len(result.issues), "; ".join(result.issues[:3]))
        rows.append(row)
        save_json(OUT/f"{name.lower()}_fine_validation.json", {
            "candidate": name, "passed": result.passed,
            "outage_s": result.outage_s, "max_step_s": .25,
            "issues": result.issues,
            "minimum_relay_access_margin_db": result.minimum_relay_access_margin_db,
            "minimum_relay_backhaul_margin_db": result.minimum_relay_backhaul_margin_db})
        print(row, flush=True)
    sheet("q3_fine_validation.xlsx", ["candidate", "status", "outage_s",
          "minimum_relay_access_margin_db", "minimum_relay_backhaul_margin_db",
          "issue_count", "issues"], rows)


if __name__ == "__main__":
    main()
