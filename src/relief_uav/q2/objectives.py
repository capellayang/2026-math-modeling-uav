"""Q2-v2 two-primary-objective comparisons and project selection rules.

These are modeling choices for the search, not formulas supplied by the problem.
Hard constraints are checked separately by the physical evaluator and validator.
"""

from math import hypot, isfinite
from typing import Iterable, TypeVar

from .model import Q2Objective, Q2Solution

T = TypeVar("T")


def _objective(value: Q2Objective | Q2Solution) -> Q2Objective:
    return value.objective if isinstance(value, Q2Solution) else value


def primary_objectives(value: Q2Objective | Q2Solution) -> tuple[float, float]:
    obj = _objective(value)
    return obj.weighted_tardiness, obj.makespan_s


def secondary_objectives(value: Q2Objective | Q2Solution) -> tuple[float, int]:
    obj = _objective(value)
    return obj.total_energy_kwh, obj.sortie_count


def _dominates(x: tuple[float, ...], y: tuple[float, ...], tolerance: float) -> bool:
    return all(a <= b + tolerance for a, b in zip(x, y)) and any(
        a < b - tolerance for a, b in zip(x, y))


def dominates_primary(a: Q2Objective | Q2Solution, b: Q2Objective | Q2Solution,
                      *, tolerance: float = 1e-7) -> bool:
    return _dominates(primary_objectives(a), primary_objectives(b), tolerance)


def dominates_full(a: Q2Objective | Q2Solution, b: Q2Objective | Q2Solution,
                   *, tolerance: float = 1e-7) -> bool:
    return _dominates(primary_objectives(a) + secondary_objectives(a),
                      primary_objectives(b) + secondary_objectives(b), tolerance)


def tardiness_limit(best_tardiness: float, relative_epsilon: float,
                    absolute_epsilon: float = 0.0) -> float:
    if any(not isfinite(v) or v < 0 for v in
           (best_tardiness, relative_epsilon, absolute_epsilon)):
        raise ValueError("Tardiness and epsilon values must be finite and nonnegative")
    return best_tardiness * (1.0 + relative_epsilon) + absolute_epsilon


def epsilon_feasible(value: Q2Objective | Q2Solution, best_tardiness: float,
                     relative_epsilon: float, absolute_epsilon: float = 0.0,
                     *, tolerance: float = 1e-7) -> bool:
    return primary_objectives(value)[0] <= tardiness_limit(
        best_tardiness, relative_epsilon, absolute_epsilon) + tolerance


def normalize_objectives(values: Iterable[Q2Objective | Q2Solution]
                         ) -> list[tuple[float, float]]:
    pairs = [primary_objectives(value) for value in values]
    if not pairs:
        return []
    minima = tuple(min(pair[i] for pair in pairs) for i in range(2))
    maxima = tuple(max(pair[i] for pair in pairs) for i in range(2))
    return [tuple(0.0 if maxima[i] - minima[i] <= 1e-12 else
                  (pair[i] - minima[i]) / (maxima[i] - minima[i])
                  for i in range(2)) for pair in pairs]


def pareto_filter(values: Iterable[T], *, objective=lambda row: row,
                  tolerance: float = 1e-7) -> list[T]:
    """Primary J1/J2 front; primary ties keep best J3/J4 representative."""
    rows = list(values)
    front = []
    for i, row in enumerate(rows):
        value = objective(row)
        if any(i != j and dominates_primary(objective(other), value,
                                            tolerance=tolerance)
               for j, other in enumerate(rows)):
            continue
        tied = [other for other in front if all(abs(a - b) <= tolerance for a, b in
                zip(primary_objectives(objective(other)), primary_objectives(value)))]
        if tied:
            previous = tied[0]
            if secondary_objectives(value) < secondary_objectives(objective(previous)):
                front.remove(previous)
            else:
                continue
        front = [other for other in front if not dominates_primary(
            value, objective(other), tolerance=tolerance)]
        front.append(row)
    return front


def select_candidate(candidates: list[T], *, objective=lambda row: row,
                     method: str = "epsilon_makespan", tardiness_slack: float = 0.05,
                     absolute_epsilon: float = 0.0) -> T:
    front = pareto_filter(candidates, objective=objective)
    if not front:
        raise ValueError("No Pareto candidate to select")
    if method == "epsilon_makespan":
        best = min(primary_objectives(objective(row))[0] for row in front)
        eligible = [row for row in front if epsilon_feasible(
            objective(row), best, tardiness_slack, absolute_epsilon)]
        return min(eligible, key=lambda row: (
            primary_objectives(objective(row))[1],
            *secondary_objectives(objective(row)),
            primary_objectives(objective(row))[0]))
    if method == "ideal_distance":
        normalized = normalize_objectives(objective(row) for row in front)
        return min(zip(front, normalized), key=lambda pair: (
            hypot(*pair[1]), *secondary_objectives(objective(pair[0]))))[0]
    raise ValueError(f"Unknown Q2-v2 selection method {method}")
