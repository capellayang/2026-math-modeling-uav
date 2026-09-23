"""Project CLI for independently validated Q1, Q2 and Q3 solutions."""

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
from relief_uav.q2.model import Q2AlgorithmConfig
from relief_uav.q2.search_v2 import solve_q2_v2
from relief_uav.q2.report_v2 import benchmark_saved_v2, save_v2_outputs
from relief_uav.q2.objectives import epsilon_feasible
from relief_uav.communication.coverage import RadioEnvironment
from relief_uav.geo.dem import DigitalElevationModel
from relief_uav.geo.segments import dem_source_path
from relief_uav.q3.model import Q3AlgorithmConfig
from relief_uav.q3.solver import solve_q3
from relief_uav.q3.report import load_q3_solution, save_q3_outputs
from relief_uav.validation.q3 import validate_q3

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


def run_q2_v2(force_recompute: bool, config: Q2AlgorithmConfig) -> None:
    scenario = load_scenario(ROOT)
    segments = build_segment_matrix(scenario, force_recompute=force_recompute)
    run = solve_q2_v2(scenario, segments, config,
                      ROOT / "outputs/q2_v2/baseline_v1/q2_summary.json")
    selected = run.selected.solution
    assignments = tuple((s.spec, s.drone_id, s.battery_id, s.preparation_start_s)
                        for s in selected.sorties)
    final = build_solution(scenario, segments, assignments,
                           seed=run.selected.metadata.seed,
                           objective_mode="pareto_epsilon",
                           search_seconds=run.statistics.elapsed_s,
                           pareto_count=len(run.pareto))
    validation = validate_q2(scenario, segments, final)
    if not validation.passed:
        raise RuntimeError(f"Q2-v2 validator FAIL: {validation.issues[:8]}")
    if not epsilon_feasible(final, run.j1_best_found, config.tardiness_slack,
                            config.absolute_epsilon, tolerance=0.05):
        raise RuntimeError("Q2-v2 selected candidate violates the configured epsilon limit")
    save_v2_outputs(ROOT, run, final, validation)
    benchmark_saved_v2(ROOT)
    obj = final.objective
    print(f"Q2-v2: J1={obj.weighted_tardiness:.6f}, normalized={obj.normalized_weighted_tardiness:.9f}, "
          f"J2={obj.makespan_s:.3f} s, J3={obj.total_energy_kwh:.6f} kWh, J4={obj.sortie_count}")
    print(f"Search {run.statistics.elapsed_s:.3f} s; ALNS iterations={run.statistics.iterations}; "
          f"CP candidates={len(run.all_cp_candidates)}; Pareto={len(run.pareto)}; "
          f"selected={run.selected.metadata.candidate_id}; validator PASS")


def validate_saved_q2_v2(force_recompute: bool) -> None:
    scenario = load_scenario(ROOT)
    segments = build_segment_matrix(scenario, force_recompute=force_recompute)
    path = ROOT / "outputs/q2_v2/q2_summary.json"
    if not path.is_file():
        raise FileNotFoundError("Run python main.py --question q2 --q2-algorithm v2 first")
    saved = json.loads(path.read_text(encoding="utf-8"))
    solution = load_q2_solution(path)
    result = validate_q2(scenario, segments, solution)
    selected = saved.get("selected_candidate_id")
    front = saved.get("pareto_candidates", [])
    if selected not in {row["candidate_id"] for row in front}:
        raise RuntimeError("Saved selected candidate is missing from the Pareto front")
    if not epsilon_feasible(solution, saved["j1_best_found"], saved["epsilon_level"],
                            saved["algorithm_config"]["absolute_epsilon"], tolerance=0.05):
        raise RuntimeError("Saved Q2-v2 selected solution exceeds its epsilon limit")
    print(f"Q2-v2 saved-plan validation: {'PASS' if result.passed else 'FAIL'}; "
          f"{result.delivered_unique_boxes}/{result.expected_boxes} boxes; "
          f"{result.checked_sorties} sorties")
    for issue in result.issues:
        print(f"  {issue.scope} | {issue.check} | {issue.detail}")
    if not result.passed:
        raise SystemExit(1)


def run_q3(force_recompute: bool, config: Q3AlgorithmConfig) -> None:
    scenario = load_scenario(ROOT)
    segments = build_segment_matrix(scenario, force_recompute=force_recompute)
    env = RadioEnvironment(scenario, DigitalElevationModel(dem_source_path(ROOT)))
    run = solve_q3(scenario, segments, env, config,
                   baseline_path=ROOT/"outputs/q3/baseline_q2_v2_summary.json")
    save_q3_outputs(ROOT, env, run.selected, run.validation, config,
                    run.pareto, run.history, run.baseline_audit)
    o = run.selected.objective
    print(f"Q3: J1={o.weighted_tardiness:.6f}, J1norm={o.normalized_weighted_tardiness:.9f}, "
          f"transport J2={o.transport_makespan_s:.3f} s, joint J2={o.joint_makespan_s:.3f} s")
    print(f"Energy transport/relay/joint={o.transport_energy_kwh:.6f}/"
          f"{o.relay_energy_kwh:.6f}/{o.joint_energy_kwh:.6f} kWh; "
          f"sorties transport/relay/joint={o.transport_sortie_count}/"
          f"{o.relay_sortie_count}/{o.joint_sortie_count}")
    print(f"ALNS {run.selected.alns_iterations} iterations; route variants examined "
          f"{run.routes_examined}; CP-SAT {run.selected.cp_sat_status}; "
          f"Pareto {len(run.pareto)}; outage={run.validation.outage_s:.6f} s; "
          "validator PASS")


def validate_saved_q3(force_recompute: bool, max_step_s: float,
                      transition_tolerance_s: float) -> None:
    scenario = load_scenario(ROOT)
    segments = build_segment_matrix(scenario, force_recompute=force_recompute)
    env = RadioEnvironment(scenario, DigitalElevationModel(dem_source_path(ROOT)))
    path = ROOT/"outputs/q3/q3_summary.json"
    if not path.is_file():
        raise FileNotFoundError("Run python main.py --question q3 first")
    solution = load_q3_solution(path)
    result = validate_q3(env, segments, solution, max_step_s=max_step_s,
                         transition_tolerance_s=transition_tolerance_s)
    print(f"Q3 saved-plan validation: {'PASS' if result.passed else 'FAIL'}; "
          f"80 boxes; {len(solution.transport.sorties)} transport sorties; "
          f"{len(solution.relays)} relay sorties; outage={result.outage_s:.6f} s")
    for issue in result.issues[:20]:
        print(f"  {issue}")
    if not result.passed:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Relief UAV mathematical modeling project")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--question", choices=["q1", "q2", "q3"], help="Run a question solver")
    action.add_argument("--validate", choices=["q1", "q2", "q3"], help="Validate saved results")
    parser.add_argument("--force-recompute", action="store_true", help="Rebuild the DEM geometry cache")
    parser.add_argument("--seed", type=int, default=20260923, help="Q2 deterministic random seed")
    parser.add_argument("--q2-time-limit", type=float, default=None,
                        help="Q2 approximate search and CP-SAT wall-time budget in seconds")
    parser.add_argument("--q2-iterations", type=int, default=None, help="Q2 route-search iteration cap")
    parser.add_argument("--q2-objective", choices=["lexicographic", "weighted"],
                        default="lexicographic", help="Q2 multi-objective mode")
    parser.add_argument("--q2-algorithm", choices=["v1", "v2"], default="v2",
                        help="Q2 version; v2 writes only outputs/q2_v2")
    parser.add_argument("--q2-restarts", type=int, default=8)
    parser.add_argument("--q2-cp-candidates", type=int, default=20)
    parser.add_argument("--q2-tardiness-slack", type=float, default=0.05)
    parser.add_argument("--q2-absolute-epsilon", type=float, default=0.0)
    parser.add_argument("--q2-selection", choices=["epsilon_makespan", "ideal_distance"],
                        default="epsilon_makespan")
    parser.add_argument("--q2-epsilon-levels", default="0,0.02,0.05,0.10")
    parser.add_argument("--q3-time-limit", type=float, default=600.0)
    parser.add_argument("--q3-iterations", type=int, default=2000)
    parser.add_argument("--q3-restarts", type=int, default=6)
    parser.add_argument("--q3-search-step", type=float, default=1.0)
    parser.add_argument("--q3-validation-step", type=float, default=0.25)
    parser.add_argument("--q3-transition-tolerance", type=float, default=0.05)
    parser.add_argument("--q3-hover-grid-m", type=float, default=600.0)
    parser.add_argument("--q3-hover-altitudes", default="50,100,150,200,250,300")
    parser.add_argument("--q3-hover-top-k", type=int, default=20)
    parser.add_argument("--q3-cp-candidates", type=int, default=12)
    parser.add_argument("--q3-tardiness-slack", type=float, default=0.05)
    parser.add_argument("--q3-absolute-epsilon", type=float, default=0.0)
    parser.add_argument("--q3-selection", choices=["epsilon_makespan"],
                        default="epsilon_makespan")
    parser.add_argument("--q3-setup-energy-mode", choices=["hover_plus_comm", "hover_only"],
                        default="hover_plus_comm")
    args = parser.parse_args()
    if args.question == "q1":
        run_q1(args.force_recompute)
    elif args.validate == "q1":
        validate_saved_q1(args.force_recompute)
    elif args.question == "q2":
        if args.q2_algorithm == "v1":
            run_q2(args.force_recompute, args.seed, args.q2_time_limit or 60.0,
                   args.q2_iterations or 400, args.q2_objective)
        else:
            levels = tuple(float(value.strip()) for value in args.q2_epsilon_levels.split(","))
            config = Q2AlgorithmConfig(seed=args.seed,
                time_limit_s=args.q2_time_limit if args.q2_time_limit is not None else 300.0,
                iteration_limit=args.q2_iterations if args.q2_iterations is not None else 3000,
                restarts=args.q2_restarts, cp_candidates=args.q2_cp_candidates,
                epsilon_levels=levels, tardiness_slack=args.q2_tardiness_slack,
                absolute_epsilon=args.q2_absolute_epsilon,
                selection_method=args.q2_selection)
            run_q2_v2(args.force_recompute, config)
    elif args.validate == "q2":
        if args.q2_algorithm == "v1":
            validate_saved_q2(args.force_recompute)
        else:
            validate_saved_q2_v2(args.force_recompute)
    elif args.question == "q3":
        config = Q3AlgorithmConfig(seed=args.seed, time_limit_s=args.q3_time_limit,
            iterations=args.q3_iterations, restarts=args.q3_restarts,
            search_step_s=args.q3_search_step,
            validation_step_s=args.q3_validation_step,
            transition_tolerance_s=args.q3_transition_tolerance,
            hover_grid_m=args.q3_hover_grid_m,
            hover_altitudes_m=tuple(float(x) for x in args.q3_hover_altitudes.split(",")),
            hover_top_k=args.q3_hover_top_k,
            cp_candidates=args.q3_cp_candidates,
            tardiness_slack=args.q3_tardiness_slack,
            absolute_epsilon=args.q3_absolute_epsilon,
            selection=args.q3_selection,
            relay_setup_energy_mode=args.q3_setup_energy_mode)
        run_q3(args.force_recompute, config)
    elif args.validate == "q3":
        validate_saved_q3(args.force_recompute, args.q3_validation_step,
                          args.q3_transition_tolerance)


if __name__ == "__main__":
    main()
