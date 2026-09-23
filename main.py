"""Project CLI for independently validated Q1 and Q2 solutions."""

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
from relief_uav.validation import validate_q2
from relief_uav.q2.report import load_q2_solution, save_q2_outputs
from relief_uav.q2.search import search_routes, save_search_history
from relief_uav.q2.timeline import build_solution

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


def run_q2(force_recompute: bool, seed: int, time_limit_s: float,
           iteration_limit: int, objective_mode: str) -> None:
    scenario = load_scenario(ROOT)
    segments = build_segment_matrix(scenario, force_recompute=force_recompute)
    preliminary, pareto, search_pareto, history, elapsed, cp_status = search_routes(
        scenario, segments, seed=seed, time_limit_s=time_limit_s,
        iteration_limit=iteration_limit, objective_mode=objective_mode)
    assignments = tuple((s.spec, s.drone_id, s.battery_id, s.preparation_start_s)
                        for s in preliminary.sorties)
    solution = build_solution(scenario, segments, assignments, seed=seed,
                              objective_mode=objective_mode, search_seconds=elapsed,
                              pareto_count=len(pareto))
    validation = validate_q2(scenario, segments, solution)
    if not validation.passed:
        raise RuntimeError(f"Q2 independent validation failed: {validation.issues[:10]}")
    save_search_history(ROOT / "outputs/logs/q2_search_history.csv", history)
    save_q2_outputs(ROOT, solution, validation, pareto, search_pareto, history, cp_status)
    objective = solution.objective
    print(f"Q2: {objective.sortie_count} sorties; weighted tardiness="
          f"{objective.weighted_tardiness:.6f}; normalized="
          f"{objective.normalized_weighted_tardiness:.9f}; makespan="
          f"{objective.makespan_s:.3f} s; energy={objective.total_energy_kwh:.6f} kWh")
    print(f"Search {elapsed:.3f} s; CP-SAT {cp_status}; Pareto CP={len(pareto)}, "
          f"fast archive={len(search_pareto)}; validator PASS")


def validate_saved_q2(force_recompute: bool) -> None:
    scenario = load_scenario(ROOT)
    segments = build_segment_matrix(scenario, force_recompute=force_recompute)
    path = ROOT / "outputs/q2/q2_summary.json"
    if not path.is_file():
        raise FileNotFoundError("Run python main.py --question q2 first")
    solution = load_q2_solution(path)
    result = validate_q2(scenario, segments, solution)
    print(f"Q2 saved-plan validation: {'PASS' if result.passed else 'FAIL'}; "
          f"{result.delivered_unique_boxes}/{result.expected_boxes} boxes; "
          f"{result.checked_sorties} sorties")
    for issue in result.issues:
        print(f"  {issue.scope} | {issue.check} | {issue.detail}")
    if not result.passed:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Relief UAV mathematical modeling project")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--question", choices=["q1", "q2"], help="Run a question solver")
    action.add_argument("--validate", choices=["q1", "q2"], help="Validate saved results")
    parser.add_argument("--force-recompute", action="store_true", help="Rebuild the DEM geometry cache")
    parser.add_argument("--seed", type=int, default=20260923, help="Q2 deterministic random seed")
    parser.add_argument("--q2-time-limit", type=float, default=60.0,
                        help="Q2 approximate search and CP-SAT wall-time budget in seconds")
    parser.add_argument("--q2-iterations", type=int, default=400, help="Q2 route-search iteration cap")
    parser.add_argument("--q2-objective", choices=["lexicographic", "weighted"],
                        default="lexicographic", help="Q2 multi-objective mode")
    args = parser.parse_args()
    if args.question == "q1":
        run_q1(args.force_recompute)
    elif args.validate == "q1":
        validate_saved_q1(args.force_recompute)
    elif args.question == "q2":
        run_q2(args.force_recompute, args.seed, args.q2_time_limit,
               args.q2_iterations, args.q2_objective)
    elif args.validate == "q2":
        validate_saved_q2(args.force_recompute)


if __name__ == "__main__":
    main()
