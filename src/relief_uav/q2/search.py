"""Multi-start route neighborhoods and deterministic fast scheduling."""

from dataclasses import dataclass
import csv
import json
from pathlib import Path
import random
from time import monotonic

from relief_uav.data.models import Scenario
from relief_uav.geo.segments import SegmentMatrix
from relief_uav.physics.battery import charge_to_full_s
from relief_uav.physics.sortie import SortieInputError, evaluate_transport_sortie
from relief_uav.q1 import solve_q1
from .model import Q2SortieSpec, StopAssignment
from .scheduler import schedule_cp_sat
from .timeline import MEDICAL_TYPE, battery_inventory, build_solution


@dataclass(frozen=True)
class SearchRecord:
    iteration: int
    operator: str
    accepted: bool
    feasible_fast: bool
    weighted_tardiness: float | None
    makespan_s: float | None
    energy_kwh: float | None
    sortie_count: int
    best_weighted_tardiness: float | None


def make_spec(model: str, mapping: dict[str, tuple[str, ...]],
              order: tuple[str, ...], ident: str = "pending") -> Q2SortieSpec:
    return Q2SortieSpec(ident, model, ("O01", *order, "O01"),
                        tuple(StopAssignment(s, tuple(sorted(mapping[s]))) for s in order))


def number(specs: tuple[Q2SortieSpec, ...]) -> tuple[Q2SortieSpec, ...]:
    return tuple(Q2SortieSpec(f"Q2-{i:03d}", s.model_id, s.route, s.deliveries)
                 for i, s in enumerate(specs, 1))


def physical(scenario: Scenario, segments: SegmentMatrix, spec: Q2SortieSpec) -> bool:
    try:
        return evaluate_transport_sortie(scenario, segments, spec.model_id,
                                         spec.route, spec.delivery_map()).feasible
    except (SortieInputError, ValueError, KeyError):
        return False


def q1_seed(scenario: Scenario, segments: SegmentMatrix) -> tuple[Q2SortieSpec, ...]:
    path = scenario.root / "outputs/q1/q1_summary.json"
    if path.is_file():
        trips = json.loads(path.read_text(encoding="utf-8"))["trips"]
        items = [(t["model_id"], t["service_id"], tuple(t["box_ids"])) for t in trips]
    else:
        items = [(t.model_id, t.service_id, t.box_ids)
                 for t in solve_q1(scenario, segments).trips]
    return number(tuple(make_spec(m, {s: ids}, (s,)) for m, s, ids in items))


def deadline_seed(scenario: Scenario, segments: SegmentMatrix,
                  fallback: tuple[Q2SortieSpec, ...], shift: int = 0) -> tuple[Q2SortieSpec, ...]:
    """Repack hard and soft boxes independently; Q1 grouping is not fixed."""
    result = []
    early = sorted(scenario.services, key=lambda service: min(
        (min(b.desired_delivery_s if b.material_type == MEDICAL_TYPE else float("inf"),
             b.first_batch_deadline_s if b.first_batch else float("inf"))
         for b in scenario.boxes.values() if b.service_id == service), default=float("inf")))
    model_cycle = ("A", "A", "A", "A", "B", "B", "C", "C")
    for service_index, service in enumerate(early):
        boxes = sorted((b for b in scenario.boxes.values() if b.service_id == service),
                       key=lambda b: (min(b.desired_delivery_s if b.material_type == MEDICAL_TYPE else float("inf"),
                                          b.first_batch_deadline_s if b.first_batch else float("inf")),
                                      -b.emergency_priority, b.box_id))
        hard = [b.box_id for b in boxes if b.first_batch or b.material_type == MEDICAL_TYPE]
        soft = [b.box_id for b in boxes if b.box_id not in hard]
        for group_index, group in enumerate((hard, soft)):
            remaining = list(group)
            while remaining:
                best = None
                if group_index == 0:
                    preferred = model_cycle[(service_index + shift) % len(model_cycle)]
                    models = (preferred,) + tuple(m for m in ("A", "B", "C") if m != preferred)
                else:
                    models = ("A", "B", "C") if shift == 4 else ("C", "B", "A")
                for model in models:
                    packed = []
                    for box_id in remaining:
                        if physical(scenario, segments, make_spec(model,
                                {service: tuple(packed + [box_id])}, (service,))):
                            packed.append(box_id)
                    if packed and best is None:
                        best = (model, packed)
                if best is None:
                    return fallback
                result.append(make_spec(best[0], {service: tuple(best[1])}, (service,)))
                remaining = [b for b in remaining if b not in best[1]]
    return number(tuple(result))


def hard_deadline(scenario: Scenario, spec: Q2SortieSpec) -> float:
    bounds = []
    for stop in spec.deliveries:
        for box_id in stop.box_ids:
            box = scenario.boxes[box_id]
            if box.material_type == MEDICAL_TYPE:
                bounds.append(box.desired_delivery_s)
            if box.first_batch:
                bounds.append(box.first_batch_deadline_s)
    return min(bounds, default=float("inf"))


def fast_schedule(scenario: Scenario, segments: SegmentMatrix,
                  specs: tuple[Q2SortieSpec, ...], rng: random.Random | None = None):
    """Fast estimate only; an infeasible list schedule is not a route proof."""
    drones = {d: 0.0 for d in scenario.transport_drones}
    batteries = {b: 0.0 for b in battery_inventory(scenario)}
    inventory = battery_inventory(scenario)
    items = list(specs)
    if rng:
        rng.shuffle(items)
    items.sort(key=lambda s: (hard_deadline(scenario, s),
                              -max(scenario.boxes[b].emergency_priority
                                   for stop in s.deliveries for b in stop.box_ids)))
    assignments = []
    for spec in items:
        evaluation = evaluate_transport_sortie(scenario, segments, spec.model_id,
                                                spec.route, spec.delivery_map())
        choices = [(max(drones[d], batteries[b]), d, b)
                   for d, drone in scenario.transport_drones.items()
                   if drone.model_id == spec.model_id
                   for b, model in inventory.items() if model == spec.model_id]
        start, drone_id, battery_id = min(choices)
        finish = start + evaluation.total_operation_s
        drones[drone_id] = finish
        batteries[battery_id] = finish + charge_to_full_s(
            evaluation.return_soc, scenario.battery_stocks[spec.model_id].full_charge_s)
        assignments.append((spec, drone_id, battery_id, start))
    return build_solution(scenario, segments, tuple(assignments))


def hard_feasible(scenario: Scenario, solution) -> bool:
    for d in solution.box_deliveries:
        box = scenario.boxes[d.box_id]
        if box.material_type == MEDICAL_TYPE and d.delivery_complete_time_s > box.desired_delivery_s + 1e-8:
            return False
        if box.first_batch and d.delivery_complete_time_s > box.first_batch_deadline_s + 1e-8:
            return False
    return True


def mutate(scenario: Scenario, segments: SegmentMatrix,
           specs: tuple[Q2SortieSpec, ...], rng: random.Random):
    operations = ("merge", "split", "relocate_box", "swap_boxes", "move_partial",
                  "insert_service", "remove_reinsert", "reorder", "two_opt",
                  "change_model", "priority_order")
    operation = rng.choice(operations)
    arr = list(specs)
    if operation == "priority_order":
        rng.shuffle(arr)
        return number(tuple(arr)), operation
    if operation == "change_model":
        i = rng.randrange(len(arr))
        old = arr[i]
        arr[i] = make_spec(rng.choice([m for m in scenario.transport_models if m != old.model_id]),
                           old.delivery_map(), old.route[1:-1])
    elif operation in ("reorder", "two_opt", "remove_reinsert"):
        possible = [i for i, s in enumerate(arr) if len(s.deliveries) > 1]
        if not possible:
            return specs, operation
        i = rng.choice(possible)
        old = arr[i]
        order = list(old.route[1:-1])
        if operation == "two_opt":
            a, b = sorted(rng.sample(range(len(order)), 2))
            order[a:b + 1] = reversed(order[a:b + 1])
        else:
            moved = order.pop(rng.randrange(len(order)))
            order.insert(rng.randrange(len(order) + 1), moved)
        arr[i] = make_spec(old.model_id, old.delivery_map(), tuple(order))
    elif operation == "split":
        i = rng.randrange(len(arr))
        old = arr[i]
        mapping = old.delivery_map()
        if len(mapping) > 1:
            service = rng.choice(list(mapping))
            moved = mapping.pop(service)
        else:
            service = next(iter(mapping))
            ids = mapping[service]
            if len(ids) < 2:
                return specs, operation
            cut = rng.randrange(1, len(ids))
            moved, mapping[service] = ids[:cut], ids[cut:]
        arr[i] = make_spec(old.model_id, mapping,
                           tuple(s for s in old.route[1:-1] if s in mapping))
        arr.append(make_spec(old.model_id, {service: moved}, (service,)))
    else:
        if len(arr) < 2:
            return specs, operation
        a, b = rng.sample(range(len(arr)), 2)
        first, second = arr[a], arr[b]
        left, right = first.delivery_map(), second.delivery_map()
        if operation == "merge":
            for service, ids in right.items():
                left[service] = left.get(service, ()) + ids
            order = tuple(dict.fromkeys((*first.route[1:-1], *second.route[1:-1])))
            arr[a] = make_spec(rng.choice((first.model_id, second.model_id, "C")), left, order)
            del arr[b]
        else:
            if operation == "swap_boxes":
                s1, s2 = rng.choice(list(left)), rng.choice(list(right))
                id1, id2 = rng.choice(left[s1]), rng.choice(right[s2])
                left[s1] = tuple(x for x in left[s1] if x != id1)
                right[s2] = tuple(x for x in right[s2] if x != id2)
                left[s2] = left.get(s2, ()) + (id2,)
                right[s1] = right.get(s1, ()) + (id1,)
            else:
                service = rng.choice(list(left))
                ids = left[service]
                if operation == "move_partial" and len(ids) > 1:
                    moved = ids[:rng.randrange(1, len(ids))]
                elif operation == "insert_service":
                    moved = ids
                else:
                    moved = (rng.choice(ids),)
                left[service] = tuple(x for x in ids if x not in moved)
                right[service] = right.get(service, ()) + moved
            left = {s: ids for s, ids in left.items() if ids}
            right = {s: ids for s, ids in right.items() if ids}
            arr[a] = (make_spec(first.model_id, left,
                      tuple(s for s in first.route[1:-1] if s in left) +
                      tuple(s for s in left if s not in first.route[1:-1])) if left else None)
            order = (tuple(s for s in second.route[1:-1] if s in right) +
                     tuple(s for s in right if s not in second.route[1:-1]))
            if operation == "insert_service" and len(order) > 1:
                order = tuple(rng.sample(order, len(order)))
            arr[b] = make_spec(second.model_id, right, order)
            arr = [s for s in arr if s is not None]
    if all(physical(scenario, segments, s) for s in arr):
        return number(tuple(arr)), operation
    return specs, operation


def dominates(a, b) -> bool:
    x, y = a.objective.lex_key, b.objective.lex_key
    return all(v <= w + 1e-8 for v, w in zip(x, y)) and any(
        v < w - 1e-8 for v, w in zip(x, y))


def objective_key(solution, mode: str):
    if mode == "lexicographic":
        return solution.objective.lex_key
    if mode == "weighted":
        objective = solution.objective
        return (objective.weighted_tardiness + objective.makespan_s / 100 +
                10 * objective.total_energy_kwh + 10 * objective.sortie_count,)
    raise ValueError(f"Unknown Q2 objective mode {mode}")


def search_routes(scenario: Scenario, segments: SegmentMatrix, *,
                  seed: int = 20260923, time_limit_s: float = 60.0,
                  iteration_limit: int = 400, objective_mode: str = "lexicographic"):
    rng = random.Random(seed)
    started = monotonic()
    q1 = q1_seed(scenario, segments)
    deadline = deadline_seed(scenario, segments, q1)
    seeds = [q1, deadline]
    seeds.extend(deadline_seed(scenario, segments, q1, shift) for shift in (1, 2, 3, 4))
    for _ in range(3):
        perturbed = deadline
        for __ in range(8):
            perturbed, _ = mutate(scenario, segments, perturbed, rng)
        seeds.append(perturbed)
    archive, history, best = [], [], None
    for iteration in range(iteration_limit):
        if monotonic() - started > time_limit_s * 0.55:
            break
        if iteration < len(seeds):
            candidate, operator = seeds[iteration], "seed"
        else:
            base = rng.choice(archive)[0] if archive and rng.random() < 0.4 else (
                best[0] if best else seeds[iteration % len(seeds)])
            candidate, operator = mutate(scenario, segments, base, rng)
        try:
            estimated = fast_schedule(scenario, segments, candidate, rng)
            feasible = hard_feasible(scenario, estimated)
        except (SortieInputError, ValueError, KeyError):
            continue
        accepted = False
        if feasible:
            if best is None or objective_key(estimated, objective_mode) < objective_key(best[1], objective_mode):
                best, accepted = (candidate, estimated), True
            if not any(dominates(sol, estimated) for _, sol in archive):
                archive = [(route, sol) for route, sol in archive if not dominates(estimated, sol)]
                archive.append((candidate, estimated))
                archive.sort(key=lambda row: row[1].objective.lex_key)
                archive = archive[:30]
        history.append(SearchRecord(iteration, operator, accepted, feasible,
                                    estimated.objective.weighted_tardiness,
                                    estimated.objective.makespan_s,
                                    estimated.objective.total_energy_kwh, len(candidate),
                                    None if best is None else best[1].objective.weighted_tardiness))
    candidates = [route for route, _ in archive] + seeds
    seen, unique = set(), []
    for route in candidates:
        signature = tuple((s.model_id, s.route, s.deliveries) for s in route)
        if signature not in seen:
            seen.add(signature)
            unique.append(route)
    cp_results = []
    cp_budget = max(5.0, time_limit_s * 0.45)
    for index, specs in enumerate(unique[:8]):
        scheduled = schedule_cp_sat(scenario, segments, specs,
                                    time_limit_s=max(1.0, cp_budget / min(8, len(unique))),
                                    seed=seed + index, objective_mode=objective_mode)
        if scheduled is None:
            continue
        solution = build_solution(scenario, segments, scheduled.assignments,
                                  seed=seed, objective_mode=objective_mode)
        if hard_feasible(scenario, solution):
            cp_results.append((specs, solution, scheduled.status))
    if not cp_results:
        raise RuntimeError("Q2 infeasible among searched routes: inspect capacity, volume, energy, medical/first-batch deadlines, drones, batteries and charging; hard constraints were not relaxed")
    pareto = tuple(row for row in cp_results if not any(
        dominates(other[1], row[1]) for other in cp_results))
    final = min(cp_results, key=lambda row: objective_key(row[1], objective_mode))
    return final[1], pareto, tuple(archive), tuple(history), monotonic() - started, final[2]


def save_search_history(path: Path, history: tuple[SearchRecord, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(SearchRecord.__dataclass_fields__)
        for row in history:
            writer.writerow(tuple(getattr(row, name) for name in SearchRecord.__dataclass_fields__))
