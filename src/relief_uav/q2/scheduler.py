"""CP-SAT assignment of fixed routes to real drones and compatible batteries."""

from dataclasses import dataclass
from math import ceil, floor

from ortools.sat.python import cp_model

from relief_uav.data.models import Scenario
from relief_uav.geo.segments import SegmentMatrix
from relief_uav.physics.battery import charge_to_full_s
from relief_uav.physics.sortie import evaluate_transport_sortie
from .model import Q2SortieSpec
from .timeline import MEDICAL_TYPE, battery_inventory, build_solution, delivery_offsets_s

TIME_SCALE = 1000  # All precedence and deadline constraints use milliseconds.


@dataclass(frozen=True)
class ScheduleResult:
    assignments: tuple[tuple[Q2SortieSpec, str, str, float], ...]
    status: str
    objective_bound: float | None


def _up(seconds: float) -> int:
    return ceil(seconds * TIME_SCALE - 1e-9)


def schedule_cp_sat(scenario: Scenario, segments: SegmentMatrix,
                    specs: tuple[Q2SortieSpec, ...], *, time_limit_s: float = 15.0,
                    seed: int = 20260923, objective_mode: str = "lexicographic"
                    ) -> ScheduleResult | None:
    if objective_mode not in ("lexicographic", "weighted"):
        raise ValueError("Unsupported Q2 objective mode")
    if not specs:
        return None
    inventory = battery_inventory(scenario)
    horizon = _up(max(24 * 3600, sum(2 * 3600 for _ in specs)))
    model = cp_model.CpModel()
    start = {}
    end = {}
    drones_for = {}
    batteries_for = {}
    drone_intervals = {d: [] for d in scenario.transport_drones}
    battery_intervals = {b: [] for b in inventory}
    delivery_terms = []
    for index, spec in enumerate(specs):
        evaluation = evaluate_transport_sortie(
            scenario, segments, spec.model_id, spec.route, spec.delivery_map())
        if not evaluation.feasible:
            return None
        duration = _up(evaluation.total_operation_s)
        charge = _up(charge_to_full_s(
            evaluation.return_soc, scenario.battery_stocks[spec.model_id].full_charge_s))
        start[index] = model.NewIntVar(0, horizon - duration - charge, f"start_{index}")
        end[index] = model.NewIntVar(duration, horizon, f"return_{index}")
        model.Add(end[index] == start[index] + duration)
        drones_for[index] = {}
        for drone_id, drone in scenario.transport_drones.items():
            if drone.model_id != spec.model_id:
                continue
            assigned = model.NewBoolVar(f"drone_{index}_{drone_id}")
            drones_for[index][drone_id] = assigned
            interval = model.NewOptionalIntervalVar(
                start[index], duration, end[index], assigned,
                f"drone_interval_{index}_{drone_id}")
            drone_intervals[drone_id].append(interval)
        model.AddExactlyOne(drones_for[index].values())
        batteries_for[index] = {}
        for battery_id, battery_model in inventory.items():
            if battery_model != spec.model_id:
                continue
            assigned = model.NewBoolVar(f"battery_{index}_{battery_id}")
            batteries_for[index][battery_id] = assigned
            available = model.NewIntVar(duration + charge, horizon,
                                        f"battery_available_{index}_{battery_id}")
            model.Add(available == start[index] + duration + charge)
            interval = model.NewOptionalIntervalVar(
                start[index], duration + charge, available, assigned,
                f"battery_interval_{index}_{battery_id}")
            battery_intervals[battery_id].append(interval)
        model.AddExactlyOne(batteries_for[index].values())
        for stop, offset_s in zip(spec.deliveries, delivery_offsets_s(scenario, segments, spec)):
            delivery_ms = model.NewIntVar(0, horizon, f"delivery_{index}_{stop.service_id}")
            model.Add(delivery_ms == start[index] + _up(offset_s))
            for box_id in stop.box_ids:
                box = scenario.boxes[box_id]
                if box.material_type == MEDICAL_TYPE:
                    model.Add(delivery_ms <= floor(box.desired_delivery_s * TIME_SCALE))
                if box.first_batch:
                    model.Add(delivery_ms <= floor(box.first_batch_deadline_s * TIME_SCALE))
                tardy = model.NewIntVar(0, horizon, f"tardy_{index}_{box_id}")
                model.AddMaxEquality(tardy, [0, delivery_ms - _up(box.desired_delivery_s)])
                priority = round(box.emergency_priority * 100)
                delivery_terms.append(tardy * priority)
    for intervals in drone_intervals.values():
        if len(intervals) > 1:
            model.AddNoOverlap(intervals)
    for intervals in battery_intervals.values():
        if len(intervals) > 1:
            model.AddNoOverlap(intervals)
    makespan = model.NewIntVar(0, horizon, "makespan")
    model.AddMaxEquality(makespan, list(end.values()))
    weighted_tardiness = sum(delivery_terms)
    if objective_mode == "lexicographic":
        model.Minimize(weighted_tardiness)
    else:
        model.Minimize(weighted_tardiness * 100 + makespan)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.1, time_limit_s * (0.65 if objective_mode == "lexicographic" else 1.0))
    solver.parameters.num_search_workers = 8
    solver.parameters.random_seed = seed
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    if objective_mode == "lexicographic" and time_limit_s > 1:
        first_assignments = tuple((spec,
            next(d for d, var in drones_for[index].items() if solver.Value(var)),
            next(b for b, var in batteries_for[index].items() if solver.Value(var)),
            solver.Value(start[index]) / TIME_SCALE)
            for index, spec in enumerate(specs))
        first_status = status
        first_bound = solver.BestObjectiveBound()
        model.Add(weighted_tardiness <= round(solver.ObjectiveValue()))
        model.Minimize(makespan)
        second = cp_model.CpSolver()
        second.parameters.max_time_in_seconds = max(0.1, time_limit_s * 0.35)
        second.parameters.num_search_workers = 8
        second.parameters.random_seed = seed
        second_status = second.Solve(model)
        if second_status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            solver, status = second, second_status
        else:
            return ScheduleResult(first_assignments,
                                  f"{solver.StatusName(first_status)}; second stage unavailable",
                                  first_bound)
    assignments = tuple((spec,
                         next(d for d, var in drones_for[index].items() if solver.Value(var)),
                         next(b for b, var in batteries_for[index].items() if solver.Value(var)),
                         solver.Value(start[index]) / TIME_SCALE)
                        for index, spec in enumerate(specs))
    # Verify original floating times immediately; conservatively rounded CP-SAT
    # durations can only make intervals longer, never hide a conflict.
    solution = build_solution(scenario, segments, assignments)
    if any(s.return_o01_time_s > 24 * 3600 * len(specs) for s in solution.sorties):
        raise AssertionError("Unexpected schedule horizon")
    return ScheduleResult(assignments, solver.StatusName(status), solver.BestObjectiveBound())


def schedule_cp_sat_epsilon(*args, **kwargs):
    """Q2-v2 epsilon scheduler; legacy `schedule_cp_sat` remains unchanged."""
    from .scheduler_v2 import schedule_epsilon
    return schedule_epsilon(*args, **kwargs)
