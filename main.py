"""Project CLI. Only Q1 is implemented at this stage."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from relief_uav.data import load_scenario
from relief_uav.geo import build_segment_matrix
from relief_uav.q1 import Q1Solution, Q1Trip, SafePayload, solve_q1
from relief_uav.q1.report import save_q1_solution, save_sensitivity
from relief_uav.validation import validate_q1

MARGINS = (0.10, 0.15, 0.20, 0.25, 0.30)


def _load_saved_solution(path: Path) -> Q1Solution:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Q1Solution(data["safety_margin_soc"], data["objective_mode"],
                      tuple(SafePayload(**row) for row in data["safe_payloads"]),
                      tuple(Q1Trip(**{**row, "box_ids": tuple(row["box_ids"])})
                            for row in data["trips"]),
                      data["total_trips"], data["total_energy_kwh"],
                      data["total_operation_s"])


def run_q1(force_recompute: bool) -> None:
    scenario = load_scenario(ROOT)
    segments = build_segment_matrix(scenario, force_recompute=force_recompute)
    print(f"Geometry: {len(segments.records)} directed pairs; cache hit={segments.loaded_from_cache}")
    solutions = []
    baseline_validation = None
    for margin in MARGINS:
        solution = solve_q1(scenario, segments, margin)
        validation = validate_q1(scenario, segments, solution)
        print(f"Safety {margin:.0%}: trips={solution.total_trips}, "
              f"energy={solution.total_energy_kwh:.6f} kWh, "
              f"time={solution.total_operation_s:.3f} s, validator={'PASS' if validation.passed else 'FAIL'}")
        if not validation.passed:
            raise RuntimeError(f"Q1 validation failed at {margin:.0%}: {validation.issues[:5]}")
        solutions.append(solution)
        if margin == 0.20:
            baseline_validation = validation
    baseline = next(s for s in solutions if s.safety_margin_soc == 0.20)
    save_q1_solution(ROOT, baseline, baseline_validation)
    save_sensitivity(ROOT, solutions)
    print("Q1 outputs saved under outputs/q1, outputs/figures and outputs/validation")


def validate_saved_q1(force_recompute: bool) -> None:
    scenario = load_scenario(ROOT)
    segments = build_segment_matrix(scenario, force_recompute=force_recompute)
    path = ROOT / "outputs/q1/q1_summary.json"
    if not path.is_file():
        raise FileNotFoundError("Run python main.py --question q1 first")
    solution = _load_saved_solution(path)
    result = validate_q1(scenario, segments, solution)
    print(f"Q1 saved-plan validation: {'PASS' if result.passed else 'FAIL'}; "
          f"{result.delivered_unique_boxes}/{result.expected_boxes} boxes; "
          f"{result.checked_trips} trips")
    for issue in result.issues:
        print(f"  {issue.trip_id} | {issue.check} | {issue.detail}")
    if not result.passed:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Relief UAV mathematical modeling project")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--question", choices=["q1"], help="Run a question solver")
    action.add_argument("--validate", choices=["q1"], help="Validate saved results")
    parser.add_argument("--force-recompute", action="store_true", help="Rebuild the DEM geometry cache")
    args = parser.parse_args()
    if args.question == "q1":
        run_q1(args.force_recompute)
    elif args.validate == "q1":
        validate_saved_q1(args.force_recompute)


if __name__ == "__main__":
    main()
