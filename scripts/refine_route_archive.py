"""CP-refine selected route archive entries and admit only validated Q3/Q4 plans."""

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
from relief_uav.q2.model import Q2SortieSpec, StopAssignment
from relief_uav.q2.scheduler_v2 import schedule_epsilon
from relief_uav.q2.timeline import build_solution
from relief_uav.q3.audit import direct_audit
from relief_uav.q3.candidates import generate_candidates
from relief_uav.q3.joint_cp import schedule_joint
from relief_uav.q3.model import Q3AlgorithmConfig
from relief_uav.q3.report import load_q3_solution
from relief_uav.q3.timeline import build_q3_solution
from relief_uav.validation.q2 import validate_q2
from relief_uav.validation.q3 import validate_q3


def specs_of(raw):
    return tuple(Q2SortieSpec(x["sortie_id"], x["model_id"], tuple(x["route"]),
                tuple(StopAssignment(y["service_id"], tuple(y["box_ids"]))
                      for y in x["deliveries"])) for x in raw)


def main():
    scenario = load_scenario(ROOT)
    segments = build_segment_matrix(scenario)
    env = RadioEnvironment(scenario, DigitalElevationModel(dem_source_path(ROOT)))
    selected = (("scalar", 0), ("multiobjective", 3), ("multiobjective", 4))
    rows = []
    for mode, index in selected:
        name = f"ROUTE_{mode.upper()}_{index:02d}"
        saved_source = OUT/f"{name.lower()}_q3.json"
        if saved_source.is_file():
            saved_q3 = load_q3_solution(saved_source)
            saved_check = validate_q3(env, segments, saved_q3, max_step_s=4)
            if saved_check.passed:
                saved_q4 = q4_rows(scenario, saved_q3, saved_source, saved_check)
                save_json(OUT/f"{name.lower()}_q4.json", asdict(saved_q4))
                k2 = q4_key(saved_q4, "strict_no_duplication", 2)
                k3 = q4_key(saved_q4, "strict_no_duplication", 3)
                rows.append((name, "PASS@4s", saved_q3.objective.weighted_tardiness,
                    saved_q3.objective.joint_makespan_s,
                    saved_q3.objective.joint_energy_kwh, len(saved_q3.relays),
                    k2["components"], k2["gap"], k2["cv"],
                    k3["gap"], k3["cv"]))
                print(name, rows[-1], flush=True)
                continue
        archive = json.loads((OUT/f"route_archive_{mode}.json").read_text(encoding="utf-8"))
        entry = archive["candidates"][index]
        specs = specs_of(entry["specs"])
        cp = schedule_epsilon(scenario, segments, specs, mode="makespan",
            time_limit_s=15, seed=20260923+index, best_tardiness_integer=0)
        if cp is None:
            rows.append((name, "NO_Q2_CP", None, None, None, None, None, None))
            continue
        transport = build_solution(scenario, segments, cp.assignments,
                                   objective_mode="pareto_epsilon")
        if not validate_q2(scenario, segments, transport).passed:
            rows.append((name, "Q2_FAIL", None, None, None, None, None, None))
            continue
        audit = direct_audit(env, transport, step_s=4)
        config = replace(Q3AlgorithmConfig(), max_relay_sorties=8)
        hover, _, _ = generate_candidates(env, transport, audit, config,
                                           CACHE/f"route_hover_{mode}_{index}.json")
        try:
            joint = schedule_joint(env, segments, transport, audit, hover,
                                   config, time_limit_s=30)
        except ValueError:
            joint = None
        if joint is None:
            rows.append((name, "NO_Q3_CP", None, None, None, None, None, None))
            continue
        q3 = build_q3_solution(env, joint.transport, joint.relays,
                                seed=config.seed, search_seconds=0,
                                cp_sat_status=joint.cp_sat_status,
                                coverage_step_s=None)
        check = validate_q3(env, segments, q3, max_step_s=4)
        if not check.passed:
            rows.append((name, "Q3_FAIL", None, None, None, None, None, None))
            continue
        q3 = replace(q3, communication=check.intervals)
        source = OUT/f"{name.lower()}_q3.json"
        save_json(source, asdict(q3))
        q4 = q4_rows(scenario, q3, source, check)
        save_json(OUT/f"{name.lower()}_q4.json", asdict(q4))
        k2 = q4_key(q4, "strict_no_duplication", 2)
        k3 = q4_key(q4, "strict_no_duplication", 3)
        rows.append((name, "PASS@4s", q3.objective.weighted_tardiness,
                     q3.objective.joint_makespan_s, q3.objective.joint_energy_kwh,
                     len(q3.relays), k2["components"], k2["gap"], k2["cv"],
                     k3["gap"], k3["cv"]))
        print(name, rows[-1], flush=True)
    headers = ["candidate", "status", "J1", "J2_s", "J3_kwh", "relay_sorties",
               "strict_components", "K2_gap", "K2_CV", "K3_gap", "K3_CV"]
    sheet("q3_route_refinement.xlsx", headers,
          [list(row)+[None]*(len(headers)-len(row)) for row in rows])


if __name__ == "__main__":
    main()
