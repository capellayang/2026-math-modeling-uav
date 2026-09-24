"""Validator-guided retry: preserve full-altitude and incumbent hover diversity."""

from dataclasses import asdict, replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
sys.path.insert(0, str(ROOT/"scripts"))

from q3_q4_improvement import OUT, CACHE, q4_key, q4_rows, save_json, sheet
from relief_uav.communication.coverage import RadioEnvironment
from relief_uav.data import load_scenario
from relief_uav.geo import build_segment_matrix
from relief_uav.geo.dem import DigitalElevationModel
from relief_uav.geo.segments import dem_source_path
from relief_uav.q2.report import load_q2_solution
from relief_uav.q3.joint_cp import schedule_joint
from relief_uav.q3.model import Q3AlgorithmConfig, RelayHoverPoint
from relief_uav.q3.timeline import build_q3_solution
from relief_uav.validation.q3 import validate_q3


def points(path):
    return [RelayHoverPoint(**p) for p in json.loads(path.read_text(
        encoding="utf-8"))["points"]]


def main():
    scenario = load_scenario(ROOT)
    segments = build_segment_matrix(scenario)
    env = RadioEnvironment(scenario, DigitalElevationModel(dem_source_path(ROOT)))
    transport = load_q2_solution(ROOT/"outputs/q3/baseline_q2_v2_summary.json")
    audit = json.loads((ROOT/"outputs/q3/baseline_q2_direct_audit.json").read_text(encoding="utf-8"))
    full = points(CACHE/"hover_local_full_600_20.json")
    legacy = points(CACHE/"hover_local_legacy_600_20.json")
    unique = tuple(dict.fromkeys(full+legacy))
    config = replace(Q3AlgorithmConfig(), hover_altitude_mode="full",
                     max_relay_sorties=8)
    joint = schedule_joint(env, segments, transport, audit, unique, config,
                           time_limit_s=90)
    if joint is None:
        print("NO_CP_INCUMBENT")
        return
    q3 = build_q3_solution(env, joint.transport, joint.relays,
                            seed=config.seed, search_seconds=0,
                            cp_sat_status=joint.cp_sat_status,
                            coverage_step_s=None)
    save_json(OUT/"altitude_search_retry_unvalidated_q3.json", asdict(q3))
    check = validate_q3(env, segments, q3, max_step_s=4)
    if not check.passed:
        save_json(OUT/"altitude_search_retry_failure.json", {
            "issues": check.issues, "intervals": [asdict(x) for x in check.intervals
                  if x.mode == "OUTAGE"]})
        print("Q3_FAIL", check.issues[:5])
        return
    q3 = replace(q3, communication=check.intervals)
    source = OUT/"altitude_search_retry_q3.json"
    save_json(source, asdict(q3))
    q4 = q4_rows(scenario, q3, source, check)
    save_json(OUT/"altitude_search_retry_q4.json", asdict(q4))
    k2 = q4_key(q4, "strict_no_duplication", 2)
    k3 = q4_key(q4, "strict_no_duplication", 3)
    row = ("ALTITUDE_SEARCH_RETRY", "PASS@4s", len(unique),
           len({p.hover_agl_m for p in unique}), q3.objective.weighted_tardiness,
           q3.objective.joint_makespan_s, q3.objective.joint_energy_kwh,
           len(q3.relays), k2["components"], k2["gap"], k2["cv"],
           k3["gap"], k3["cv"])
    sheet("q3_altitude_retry.xlsx", ["method", "status", "hover_candidates",
          "altitude_count", "J1", "J2", "J3", "relay_sorties",
          "strict_components", "K2_gap", "K2_CV", "K3_gap", "K3_CV"], [row])
    print(row, flush=True)


if __name__ == "__main__":
    main()
