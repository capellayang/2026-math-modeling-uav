"""Q2-v2 multi-start ALNS, diverse CP-SAT candidates and epsilon sweep."""

from dataclasses import dataclass, replace
from math import ceil, exp, inf
from pathlib import Path
import random
from time import monotonic

from relief_uav.data.models import Scenario
from relief_uav.geo.segments import SegmentMatrix
from relief_uav.validation.q2 import validate_q2
from .alns import (AdaptiveWeights, DESTROY_OPERATORS, REPAIR_OPERATORS,
                   destroy, fast_schedule_cached, repair)
from .cache import SortieEvaluationCache
from .model import (Q2AlgorithmConfig, Q2ParetoCandidate, Q2SearchStatistics,
                    Q2Solution, Q2SortieSpec)
from .objectives import (dominates_primary, epsilon_feasible, pareto_filter,
                         primary_objectives, select_candidate)
from .report import load_q2_solution
from .scheduler import schedule_cp_sat_epsilon
from .scheduler_v2 import J1_UNIT_SCALE
from .search import deadline_seed, hard_feasible, mutate, q1_seed
from .timeline import build_solution


@dataclass(frozen=True)
class V2Candidate:
    metadata: Q2ParetoCandidate
    solution: Q2Solution
    specs: tuple[Q2SortieSpec, ...]


@dataclass(frozen=True)
class V2Run:
    baseline: Q2Solution
    selected: V2Candidate
    pareto: tuple[V2Candidate, ...]
    epsilon_results: dict[float, V2Candidate]
    all_cp_candidates: tuple[V2Candidate, ...]
    history: tuple[dict, ...]
    statistics: Q2SearchStatistics
    config: Q2AlgorithmConfig
    j1_best_found: float


def route_signature(specs: tuple[Q2SortieSpec, ...]) -> str:
    return "; ".join(f"{spec.model_id}:{'→'.join(spec.route)}:"
                     + ",".join(box_id for stop in spec.deliveries for box_id in stop.box_ids)
                     for spec in specs)


def _specs(solution: Q2Solution) -> tuple[Q2SortieSpec, ...]:
    return tuple(sortie.spec for sortie in solution.sorties)


def _new_front(archive: list[tuple], candidate: tuple, limit: int = 120) -> tuple[list[tuple], bool]:
    _, solution, _ = candidate
    if any(dominates_primary(existing[1], solution) for existing in archive):
        return archive, False
    archive = [existing for existing in archive
               if not dominates_primary(solution, existing[1])]
    # A primary tie keeps the lower-energy/fewer-sortie representative.
    for index, existing in enumerate(archive):
        a, b = primary_objectives(existing[1]), primary_objectives(solution)
        if abs(a[0] - b[0]) <= 1e-7 and abs(a[1] - b[1]) <= 1e-7:
            old = existing[1].objective
            new = solution.objective
            if (new.total_energy_kwh, new.sortie_count) < (old.total_energy_kwh, old.sortie_count):
                archive[index] = candidate
                return archive, True
            return archive, False
    archive.append(candidate)
    if len(archive) > limit:
        archive = _diverse_subset(archive, limit)
    return archive, True


def _diverse_subset(rows: list[tuple], limit: int) -> list[tuple]:
    """Keep both objective extremes, then the largest normalized J1/J2 gaps."""
    if limit <= 0:
        return []
    if len(rows) <= limit:
        return rows
    by_j1 = sorted(rows, key=lambda row: row[1].objective.weighted_tardiness)
    by_j2 = sorted(rows, key=lambda row: row[1].objective.makespan_s)
    selected = []
    seen = set()
    for row in (by_j1[0], by_j2[0]):
        if len(selected) >= limit:
            break
        signature = route_signature(row[0])
        if signature not in seen:
            selected.append(row)
            seen.add(signature)
    lo1, hi1 = by_j1[0][1].objective.weighted_tardiness, by_j1[-1][1].objective.weighted_tardiness
    lo2, hi2 = by_j2[0][1].objective.makespan_s, by_j2[-1][1].objective.makespan_s
    while len(selected) < limit:
        remaining = [row for row in rows if route_signature(row[0]) not in seen]
        if not remaining:
            break
        def gap(row):
            x = (row[1].objective.weighted_tardiness - lo1) / max(1.0, hi1 - lo1)
            y = (row[1].objective.makespan_s - lo2) / max(1.0, hi2 - lo2)
            return min(((x - (s[1].objective.weighted_tardiness - lo1) / max(1.0, hi1 - lo1)) ** 2 +
                        (y - (s[1].objective.makespan_s - lo2) / max(1.0, hi2 - lo2)) ** 2)
                       for s in selected)
        chosen = max(remaining, key=gap)
        selected.append(chosen)
        seen.add(route_signature(chosen[0]))
    return selected


def _search_scalar(solution: Q2Solution, reference: Q2Solution) -> float:
    # Only an ALNS acceptance heuristic; final selection uses the primary front.
    return (solution.objective.weighted_tardiness /
            max(1.0, reference.objective.weighted_tardiness) +
            solution.objective.makespan_s /
            max(1.0, reference.objective.makespan_s))


def _make_metadata(identifier: str, seed: int, epsilon: float | None,
                   solution: Q2Solution, status: str,
                   specs: tuple[Q2SortieSpec, ...], selected: bool = False
                   ) -> Q2ParetoCandidate:
    obj = solution.objective
    return Q2ParetoCandidate(identifier, seed, epsilon, obj.weighted_tardiness,
                             obj.normalized_weighted_tardiness, obj.makespan_s,
                             obj.total_energy_kwh, obj.sortie_count,
                             status, route_signature(specs), selected)


def _cp_solution(scenario: Scenario, segments: SegmentMatrix, result,
                 seed: int, elapsed: float = 0.0) -> Q2Solution | None:
    if result is None:
        return None
    solution = build_solution(scenario, segments, result.assignments,
                              seed=seed, objective_mode="pareto_epsilon",
                              search_seconds=elapsed)
    return solution if validate_q2(scenario, segments, solution).passed else None


def solve_q2_v2(scenario: Scenario, segments: SegmentMatrix,
                config: Q2AlgorithmConfig, baseline_path: Path) -> V2Run:
    if (config.restarts <= 0 or config.cp_candidates <= 0 or
            config.iteration_limit < 0 or config.time_limit_s <= 0):
        raise ValueError("Q2-v2 limits, restarts and CP candidates must be positive")
    if not 0 <= config.destroy_fraction_min <= config.destroy_fraction_max < 1:
        raise ValueError("Invalid ALNS destroy range")
    if (config.tardiness_slack < 0 or config.absolute_epsilon < 0 or
            any(level < 0 for level in config.epsilon_levels)):
        raise ValueError("Q2-v2 epsilon values must be nonnegative")
    started = monotonic()
    baseline = load_q2_solution(baseline_path)
    check = validate_q2(scenario, segments, baseline)
    if not check.passed:
        raise RuntimeError(f"Committed Q2-v1 baseline failed validation: {check.issues[:5]}")
    cache = SortieEvaluationCache(scenario, segments)
    destroy_weights = AdaptiveWeights.create(DESTROY_OPERATORS, config.reaction_factor)
    repair_weights = AdaptiveWeights.create(REPAIR_OPERATORS, config.reaction_factor)
    baseline_spec = _specs(baseline)
    initial_q1 = q1_seed(scenario, segments)
    archive = [(baseline_spec, baseline, config.seed)]
    extra = []
    history = []
    iterations = 0
    alns_deadline = started + config.time_limit_s * 0.37
    per_restart = max(1, ceil(config.iteration_limit / config.restarts))
    restarts_used = 0
    for restart in range(config.restarts):
        if monotonic() >= alns_deadline:
            break
        restarts_used += 1
        actual_seed = config.seed + restart
        rng = random.Random(actual_seed)
        if restart == 0:
            current_specs, current = baseline_spec, baseline
        else:
            current_specs = deadline_seed(scenario, segments, initial_q1,
                                          shift=(restart - 1) % 5)
            current = fast_schedule_cached(scenario, segments, current_specs, cache)
            if not hard_feasible(scenario, current):
                current_specs, current = baseline_spec, baseline
        archive, _ = _new_front(archive, (current_specs, current, actual_seed))
        temperature = config.initial_temperature
        for iteration in range(per_restart):
            if iterations >= config.iteration_limit or monotonic() >= alns_deadline:
                break
            iterations += 1
            destroy_name = destroy_weights.choose(rng)
            repair_name = repair_weights.choose(rng)
            destroy_size = max(1, ceil(len(scenario.boxes) * rng.uniform(
                config.destroy_fraction_min, config.destroy_fraction_max)))
            accepted, reason, reward = False, "infeasible", config.rewards[4]
            new_specs = None
            if iterations % 11 == 0:
                new_specs, legacy = mutate(scenario, segments, current_specs, rng)
                destroy_name, repair_name, destroy_size = "legacy_neighbor", legacy, 0
            else:
                partial, removed = destroy(scenario, segments, current_specs, current,
                                           destroy_size, destroy_name, rng, config)
                new_specs = repair(scenario, segments, partial, removed, cache, repair_name)
            candidate = None
            if new_specs is not None:
                try:
                    candidate = fast_schedule_cached(scenario, segments, new_specs, cache)
                except (ValueError, KeyError):
                    candidate = None
            if candidate is not None:
                if hard_feasible(scenario, candidate):
                    best_j1 = min(row[1].objective.weighted_tardiness for row in archive)
                    best_j2 = min(row[1].objective.makespan_s for row in archive)
                    archive, entered = _new_front(archive,
                                                   (new_specs, candidate, actual_seed))
                    previous_score = _search_scalar(current, baseline)
                    new_score = _search_scalar(candidate, baseline)
                    if entered and candidate.objective.weighted_tardiness < best_j1 - 1e-7 and candidate.objective.makespan_s < best_j2 - 1e-7:
                        reason, reward = "new_primary_best", config.rewards[0]
                    elif entered:
                        reason, reward = "pareto", config.rewards[1]
                    elif new_score < previous_score - 1e-9:
                        reason, reward = "improved_current", config.rewards[2]
                    elif rng.random() < exp(min(0.0, (previous_score - new_score) / max(1e-6, temperature))):
                        reason, reward = "annealed", config.rewards[3]
                    if reason != "infeasible":
                        current_specs, current, accepted = new_specs, candidate, True
                else:
                    # Not a proof of route infeasibility: retain diverse physical
                    # routes for later CP-SAT, ranked after fast-feasible candidates.
                    if len(extra) < 50:
                        extra.append((new_specs, candidate, actual_seed))
            if destroy_name in destroy_weights.weights:
                destroy_weights.update(destroy_name, reward)
            if repair_name in repair_weights.weights:
                repair_weights.update(repair_name, reward)
            best_j1 = min(row[1].objective.weighted_tardiness for row in archive)
            best_j2 = min(row[1].objective.makespan_s for row in archive)
            history.append({
                "restart": restart, "seed": actual_seed, "iteration": iteration,
                "global_iteration": iterations, "destroy_operator": destroy_name,
                "repair_operator": repair_name, "destroy_size": destroy_size,
                "accepted": accepted, "accept_reason": reason,
                "epsilon": "", "J1": None if candidate is None else candidate.objective.weighted_tardiness,
                "J2": None if candidate is None else candidate.objective.makespan_s,
                "J3": None if candidate is None else candidate.objective.total_energy_kwh,
                "J4": None if candidate is None else candidate.objective.sortie_count,
                "pareto_archive_size": len(archive), "best_J1": best_j1,
                "best_J2": best_j2, "temperature": temperature,
                "destroy_weight": destroy_weights.weights.get(destroy_name, ""),
                "repair_weight": repair_weights.weights.get(repair_name, ""),
            })
            temperature *= config.cooling_factor
    # Always consider the committed v1 route set. Stratification preserves both
    # J1 and J2 extremes and geographic/route diversity, not just lex top eight.
    pool = [(baseline_spec, baseline, config.seed)] + archive + extra
    unique = {}
    for row in pool:
        unique.setdefault(route_signature(row[0]), row)
    baseline_row = unique.pop(route_signature(baseline_spec))
    selected_routes = [baseline_row] + _diverse_subset(
        list(unique.values()), config.cp_candidates - 1)
    remaining_time = max(10.0, config.time_limit_s - (monotonic() - started))
    stage1_budget = max(1.0, remaining_time * 0.30 / len(selected_routes))
    stage1 = []
    all_cp = []
    for index, (specs, _, seed) in enumerate(selected_routes):
        result = schedule_cp_sat_epsilon(scenario, segments, specs,
                                         mode="tardiness", time_limit_s=stage1_budget,
                                         seed=seed + index)
        solution = _cp_solution(scenario, segments, result, seed)
        if solution is None:
            continue
        stage1.append((specs, seed, result, solution))
        metadata = _make_metadata(f"CP-{len(all_cp) + 1:03d}", seed, 0.0,
                                  solution, result.status, specs)
        all_cp.append(V2Candidate(metadata, solution, specs))
    if not stage1:
        raise RuntimeError("Q2-v2 CP-SAT found no feasible fixed-route schedule; v1 files are untouched")
    best_integer = min(row[2].weighted_tardiness_integer for row in stage1)
    best_found = min(row[3].objective.weighted_tardiness for row in stage1)
    levels = tuple(sorted(set((0.0, *config.epsilon_levels))))
    remaining_time = max(1.0, config.time_limit_s - (monotonic() - started))
    sweep_budget = max(0.2, remaining_time / max(1, len(stage1) * len(levels)))
    for epsilon in levels:
        for index, (specs, seed, first, first_solution) in enumerate(stage1):
            result = schedule_cp_sat_epsilon(
                scenario, segments, specs, mode="makespan",
                time_limit_s=sweep_budget, seed=seed + 1000 + index,
                best_tardiness_integer=best_integer,
                relative_epsilon=epsilon,
                absolute_epsilon=config.absolute_epsilon,
                hint_assignments=first.assignments)
            solution = _cp_solution(scenario, segments, result, seed)
            if solution is None or not epsilon_feasible(
                    solution, best_found, epsilon, config.absolute_epsilon,
                    tolerance=0.05):
                continue
            metadata = _make_metadata(f"CP-{len(all_cp) + 1:03d}", seed, epsilon,
                                      solution, result.status, specs)
            all_cp.append(V2Candidate(metadata, solution, specs))
    # Explore the other primary-objective extreme. With J1_best=0 and
    # absolute_epsilon=0, every relative epsilon sweep has the same zero bound;
    # unrestricted J2 solves can still reveal valid non-dominated tradeoffs.
    unrestricted_budget = max(1.0, min(5.0,
        (config.time_limit_s - (monotonic() - started)) / max(1, len(stage1))))
    for index, (specs, seed, first, _) in enumerate(stage1):
        result = schedule_cp_sat_epsilon(
            scenario, segments, specs, mode="unrestricted_makespan",
            time_limit_s=unrestricted_budget, seed=seed + 2000 + index,
            hint_assignments=first.assignments)
        solution = _cp_solution(scenario, segments, result, seed)
        if solution is None:
            continue
        metadata = _make_metadata(f"CP-{len(all_cp) + 1:03d}", seed, None,
                                  solution, result.status, specs)
        all_cp.append(V2Candidate(metadata, solution, specs))
    pareto = pareto_filter(all_cp, objective=lambda row: row.solution)
    selected = select_candidate(pareto, objective=lambda row: row.solution,
                                method=config.selection_method,
                                tardiness_slack=config.tardiness_slack,
                                absolute_epsilon=config.absolute_epsilon)
    selected = replace(selected, metadata=replace(selected.metadata, selected=True))
    pareto = [selected if row.metadata.candidate_id == selected.metadata.candidate_id else row
              for row in pareto]
    epsilon_results = {}
    for epsilon in levels:
        eligible = [row for row in all_cp if epsilon_feasible(
            row.solution, best_found, epsilon, config.absolute_epsilon,
            tolerance=0.05)]
        epsilon_results[epsilon] = min(eligible, key=lambda row: (
            row.solution.objective.makespan_s,
            row.solution.objective.total_energy_kwh,
            row.solution.objective.sortie_count,
            row.solution.objective.weighted_tardiness))
    elapsed = monotonic() - started
    statistics = Q2SearchStatistics(elapsed, iterations, restarts_used,
                                    cache.hits, cache.misses,
                                    dict(destroy_weights.weights),
                                    dict(repair_weights.weights))
    return V2Run(baseline, selected, tuple(pareto), epsilon_results,
                 tuple(all_cp), tuple(history), statistics, config, best_found)
