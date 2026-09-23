"""Q2-v2 CP-SAT: fixed-route J1 minimization and epsilon-bounded J2 minimization."""

from dataclasses import dataclass
from math import ceil, floor

from ortools.sat.python import cp_model

from relief_uav.data.models import Scenario
from relief_uav.geo.segments import SegmentMatrix
from relief_uav.physics.battery import charge_to_full_s
from relief_uav.physics.sortie import evaluate_transport_sortie
from .model import Q2SortieSpec
from .objectives import tardiness_limit
from .scheduler import TIME_SCALE
from .timeline import MEDICAL_TYPE, battery_inventory, build_solution, delivery_offsets_s

PRIORITY_SCALE = 100
J1_UNIT_SCALE = TIME_SCALE * PRIORITY_SCALE


@dataclass(frozen=True)
class V2ScheduleResult:
    assignments: tuple[tuple[Q2SortieSpec, str, str, float], ...]
    status: str
    weighted_tardiness_integer: int
    objective_bound: float


def _ceil_ms(seconds: float) -> int:
    return ceil(seconds * TIME_SCALE - 1e-9)


def schedule_epsilon(scenario: Scenario, segments: SegmentMatrix,
                     specs: tuple[Q2SortieSpec, ...], *,
                     mode: str, time_limit_s: float, seed: int,
                     best_tardiness_integer: int | None = None,
                     relative_epsilon: float = 0.0,
                     absolute_epsilon: float = 0.0,
                     hint_assignments: tuple | None = None) -> V2ScheduleResult | None:
    """Mode `tardiness` finds a feasible incumbent; `makespan` applies global J1 epsilon.

    A FEASIBLE stage-1 result is only the best solution found within its budget.
    Millisecond durations/offsets round upward; deadlines round downward.
    """
    if mode not in ("tardiness", "makespan", "unrestricted_makespan") or not specs:
        raise ValueError("Invalid Q2-v2 CP-SAT mode or empty specs")
    if mode == "makespan" and best_tardiness_integer is None:
        raise ValueError("Makespan mode requires a discovered J1 incumbent")
    inventory = battery_inventory(scenario)
    evaluations = [evaluate_transport_sortie(scenario, segments, spec.model_id,
                                              spec.route, spec.delivery_map()) for spec in specs]
    if not all(evaluation.feasible for evaluation in evaluations):
        return None
    durations = [_ceil_ms(evaluation.total_operation_s) for evaluation in evaluations]
    charges = [_ceil_ms(charge_to_full_s(
        evaluation.return_soc, scenario.battery_stocks[spec.model_id].full_charge_s))
        for spec, evaluation in zip(specs, evaluations)]
    # Serial execution and charging of all sorties fits this upper bound.
    # It is a finite CP domain, not a new 24-hour task deadline.
    horizon = sum(durations) + sum(charges) + _ceil_ms(3600)
    model = cp_model.CpModel()
    starts, returns, drone_choices, battery_choices = {}, {}, {}, {}
    drone_intervals = {ident: [] for ident in scenario.transport_drones}
    battery_intervals = {ident: [] for ident in inventory}
    tardy_terms = []
    total_priority = 0
    for index, (spec, evaluation) in enumerate(zip(specs, evaluations)):
        duration, charge = durations[index], charges[index]
        start = model.NewIntVar(0, horizon - duration - charge, f"start_{index}")
        returned = model.NewIntVar(duration, horizon, f"return_{index}")
        model.Add(returned == start + duration)
        starts[index], returns[index] = start, returned
        drone_choices[index] = {}
        for drone_id, drone in scenario.transport_drones.items():
            if drone.model_id != spec.model_id:
                continue
            selected = model.NewBoolVar(f"drone_{index}_{drone_id}")
            drone_choices[index][drone_id] = selected
            drone_intervals[drone_id].append(model.NewOptionalIntervalVar(
                start, duration, returned, selected, f"drone_use_{index}_{drone_id}"))
        model.AddExactlyOne(drone_choices[index].values())
        battery_choices[index] = {}
        for battery_id, battery_model in inventory.items():
            if battery_model != spec.model_id:
                continue
            selected = model.NewBoolVar(f"battery_{index}_{battery_id}")
            battery_choices[index][battery_id] = selected
            available = model.NewIntVar(duration + charge, horizon,
                                        f"available_{index}_{battery_id}")
            model.Add(available == start + duration + charge)
            battery_intervals[battery_id].append(model.NewOptionalIntervalVar(
                start, duration + charge, available, selected,
                f"battery_use_charge_{index}_{battery_id}"))
        model.AddExactlyOne(battery_choices[index].values())
        for stop, offset_s in zip(spec.deliveries, delivery_offsets_s(scenario, segments, spec)):
            delivered = model.NewIntVar(0, horizon, f"delivery_{index}_{stop.service_id}")
            model.Add(delivered == start + _ceil_ms(offset_s))
            for box_id in stop.box_ids:
                box = scenario.boxes[box_id]
                if box.material_type == MEDICAL_TYPE:
                    model.Add(delivered <= floor(box.desired_delivery_s * TIME_SCALE))
                if box.first_batch:
                    model.Add(delivered <= floor(box.first_batch_deadline_s * TIME_SCALE))
                tardy = model.NewIntVar(0, horizon, f"tardy_{index}_{box_id}")
                model.AddMaxEquality(tardy, [0, delivered - floor(box.desired_delivery_s * TIME_SCALE)])
                weight = round(box.emergency_priority * PRIORITY_SCALE)
                total_priority += weight
                tardy_terms.append(tardy * weight)
    for intervals in (*drone_intervals.values(), *battery_intervals.values()):
        if len(intervals) > 1:
            model.AddNoOverlap(intervals)
    makespan = model.NewIntVar(0, horizon, "makespan")
    model.AddMaxEquality(makespan, list(returns.values()))
    weighted = model.NewIntVar(0, horizon * total_priority, "weighted_tardiness")
    model.Add(weighted == sum(tardy_terms))
    if mode == "tardiness":
        model.Minimize(weighted)
    elif mode == "makespan":
        limit_actual = tardiness_limit(best_tardiness_integer / J1_UNIT_SCALE,
                                      relative_epsilon, absolute_epsilon)
        model.Add(weighted <= floor(limit_actual * J1_UNIT_SCALE + 1e-7))
        model.Minimize(makespan)
    else:
        model.Minimize(makespan)
    if hint_assignments is not None:
        hints = {spec.sortie_id: (drone, battery, start)
                 for spec, drone, battery, start in hint_assignments}
        for index, spec in enumerate(specs):
            if spec.sortie_id not in hints:
                continue
            drone_id, battery_id, start_s = hints[spec.sortie_id]
            model.AddHint(starts[index], round(start_s * TIME_SCALE))
            for ident, var in drone_choices[index].items():
                model.AddHint(var, int(ident == drone_id))
            for ident, var in battery_choices[index].items():
                model.AddHint(var, int(ident == battery_id))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.1, time_limit_s)
    solver.parameters.num_search_workers = 8
    solver.parameters.random_seed = seed
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    assignments = tuple((spec,
                         next(ident for ident, var in drone_choices[index].items()
                              if solver.Value(var)),
                         next(ident for ident, var in battery_choices[index].items()
                              if solver.Value(var)),
                         solver.Value(starts[index]) / TIME_SCALE)
                        for index, spec in enumerate(specs))
    # The independent validator is still required before final publication.
    build_solution(scenario, segments, assignments)
    return V2ScheduleResult(assignments, solver.StatusName(status),
                            solver.Value(weighted), solver.BestObjectiveBound())
