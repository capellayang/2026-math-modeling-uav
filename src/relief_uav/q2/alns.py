"""Adaptive large-neighborhood operators for Q2-v2 route search.

Relatedness and insertion costs are search heuristics, not problem physics.
Every proposed sortie is evaluated through the existing common physics API.
"""

from dataclasses import dataclass
from math import ceil, inf
import random

from relief_uav.data.models import Scenario
from relief_uav.geo.segments import SegmentMatrix
from relief_uav.physics.battery import charge_to_full_s
from .cache import SortieEvaluationCache
from .model import Q2AlgorithmConfig, Q2Solution, Q2SortieSpec
from .search import hard_deadline, make_spec, number
from .timeline import MEDICAL_TYPE, battery_inventory, build_solution

DESTROY_OPERATORS = (
    "worst_tardiness_removal", "early_deadline_removal",
    "critical_makespan_removal", "critical_battery_removal", "related_removal",
    "random_removal",
)
REPAIR_OPERATORS = (
    "best_insertion", "regret2_insertion", "deadline_first_repair",
    "makespan_balance_repair",
)


@dataclass
class AdaptiveWeights:
    names: tuple[str, ...]
    reaction_factor: float
    weights: dict[str, float]

    @classmethod
    def create(cls, names: tuple[str, ...], reaction_factor: float):
        if not 0 < reaction_factor <= 1:
            raise ValueError("reaction_factor must lie in (0,1]")
        return cls(names, reaction_factor, {name: 1.0 for name in names})

    def choose(self, rng: random.Random) -> str:
        total = sum(max(1e-6, self.weights[name]) for name in self.names)
        cursor = rng.random() * total
        for name in self.names:
            cursor -= max(1e-6, self.weights[name])
            if cursor <= 0:
                return name
        return self.names[-1]

    def update(self, name: str, reward: float) -> None:
        self.weights[name] = (1 - self.reaction_factor) * self.weights[name] + self.reaction_factor * reward


def _box_order(solution: Q2Solution, box_ids: list[str]) -> list[str]:
    present = {d.box_id for d in solution.box_deliveries}
    return [box_id for box_id in box_ids if box_id in present]


def worst_tardiness_removal(solution: Q2Solution, count: int) -> tuple[str, ...]:
    deliveries = sorted(solution.box_deliveries,
                        key=lambda d: (-d.weighted_tardiness, d.box_id))
    return tuple(d.box_id for d in deliveries[:count])


def early_deadline_removal(scenario: Scenario, solution: Q2Solution,
                           count: int, segments: SegmentMatrix) -> tuple[str, ...]:
    def score(box_id):
        box = scenario.boxes[box_id]
        deadline = min(box.desired_delivery_s,
                       box.first_batch_deadline_s if box.first_batch else inf)
        distance = segments.get("O01", box.service_id).horizontal_distance_m
        return (deadline / max(1.0, box.emergency_priority), -distance, box_id)
    return tuple(sorted((d.box_id for d in solution.box_deliveries),
                        key=score)[:count])


def critical_makespan_removal(solution: Q2Solution, count: int) -> tuple[str, ...]:
    """Target final sortie and the drone or battery chain that delayed its start."""
    latest = max(solution.sorties, key=lambda s: s.return_o01_time_s)
    cause, predecessor = critical_resource_cause(solution, latest)
    chain = [latest]
    if predecessor is not None:
        chain.append(predecessor)
    chain.extend(sorted((s for s in solution.sorties if s not in chain),
                        key=lambda s: (s.drone_id == latest.drone_id if cause == "drone"
                                       else s.battery_id == latest.battery_id,
                                       s.return_o01_time_s), reverse=True))
    return tuple(box_id for sortie in chain for stop in sortie.spec.deliveries
                 for box_id in stop.box_ids)[:count]


def critical_battery_removal(solution: Q2Solution, count: int) -> tuple[str, ...]:
    """Rank tasks by actual battery readiness delay versus drone readiness."""
    scored = []
    for sortie in solution.sorties:
        cause, predecessor = critical_resource_cause(solution, sortie)
        if cause != "battery" or predecessor is None:
            continue
        prior_drone = max((s.return_o01_time_s for s in solution.sorties
                           if s.drone_id == sortie.drone_id and
                           s.return_o01_time_s <= sortie.preparation_start_s + 1e-7 and
                           s.spec.sortie_id != sortie.spec.sortie_id), default=0.0)
        prior_charge = next(c.charge_end_s for c in solution.charge_events
                            if c.sortie_id == predecessor.spec.sortie_id)
        scored.append((prior_charge - prior_drone, predecessor, sortie))
    chain = []
    for _, predecessor, dependent in sorted(scored, key=lambda row: row[0], reverse=True):
        for candidate in (predecessor, dependent):
            if candidate not in chain:
                chain.append(candidate)
    chain.extend(sorted((s for s in solution.sorties if s not in chain),
                        key=lambda s: s.return_o01_time_s, reverse=True))
    return tuple(box_id for sortie in chain for stop in sortie.spec.deliveries
                 for box_id in stop.box_ids)[:count]


def critical_resource_cause(solution: Q2Solution, sortie):
    """Identify the last same-drone return or same-battery recharge before start."""
    earlier_drone = [s for s in solution.sorties
                     if s.drone_id == sortie.drone_id and
                     s.spec.sortie_id != sortie.spec.sortie_id and
                     s.return_o01_time_s <= sortie.preparation_start_s + 1e-7]
    previous_drone = max(earlier_drone, key=lambda s: s.return_o01_time_s,
                         default=None)
    charge_by_id = {c.sortie_id: c for c in solution.charge_events}
    earlier_battery = [s for s in solution.sorties
                       if s.battery_id == sortie.battery_id and
                       s.spec.sortie_id != sortie.spec.sortie_id and
                       charge_by_id[s.spec.sortie_id].charge_end_s <=
                       sortie.preparation_start_s + 1e-7]
    previous_battery = max(earlier_battery,
                           key=lambda s: charge_by_id[s.spec.sortie_id].charge_end_s,
                           default=None)
    drone_ready = previous_drone.return_o01_time_s if previous_drone else 0.0
    battery_ready = (charge_by_id[previous_battery.spec.sortie_id].charge_end_s
                     if previous_battery else 0.0)
    if battery_ready > drone_ready + 1e-7:
        return "battery", previous_battery
    return "drone", previous_drone


def related_removal(scenario: Scenario, segments: SegmentMatrix,
                    solution: Q2Solution, count: int, rng: random.Random,
                    config: Q2AlgorithmConfig) -> tuple[str, ...]:
    ids = [d.box_id for d in solution.box_deliveries]
    anchor = scenario.boxes[rng.choice(ids)]

    def score(box_id):
        box = scenario.boxes[box_id]
        distance = segments.get(anchor.service_id, box.service_id).horizontal_distance_m / 10000
        time = abs(anchor.desired_delivery_s - box.desired_delivery_s) / 7200
        priority = abs(anchor.emergency_priority - box.emergency_priority) / 10
        return (config.related_distance_weight * distance +
                config.related_deadline_weight * time +
                config.related_priority_weight * priority, box_id)

    return tuple(sorted(ids, key=score)[:count])


def destroy(scenario: Scenario, segments: SegmentMatrix, specs: tuple[Q2SortieSpec, ...],
            solution: Q2Solution, count: int, operator: str, rng: random.Random,
            config: Q2AlgorithmConfig) -> tuple[tuple[Q2SortieSpec, ...], tuple[str, ...]]:
    if operator == "worst_tardiness_removal":
        removed = worst_tardiness_removal(solution, count)
    elif operator == "early_deadline_removal":
        removed = early_deadline_removal(scenario, solution, count, segments)
    elif operator == "critical_makespan_removal":
        removed = critical_makespan_removal(solution, count)
    elif operator == "critical_battery_removal":
        removed = critical_battery_removal(solution, count)
    elif operator == "related_removal":
        removed = related_removal(scenario, segments, solution, count, rng, config)
    elif operator == "random_removal":
        removed = tuple(rng.sample([d.box_id for d in solution.box_deliveries], count))
    else:
        raise ValueError(f"Unknown destroy operator {operator}")
    removed_set = set(removed)
    reduced = []
    for spec in specs:
        mapping = {stop.service_id: tuple(box_id for box_id in stop.box_ids
                                           if box_id not in removed_set)
                   for stop in spec.deliveries}
        mapping = {service: ids for service, ids in mapping.items() if ids}
        if mapping:
            order = tuple(service for service in spec.route[1:-1] if service in mapping)
            reduced.append(make_spec(spec.model_id, mapping, order))
    return number(tuple(reduced)), removed


def _delivery_offset(evaluation, service_id: str) -> float:
    elapsed = evaluation.preparation_s
    for leg, stop in zip(evaluation.legs, evaluation.stops):
        elapsed += leg.flight_time_s + stop.handover_s
        if stop.service_id == service_id:
            return elapsed
    raise KeyError(service_id)


def _options(scenario: Scenario, segments: SegmentMatrix,
             specs: tuple[Q2SortieSpec, ...], box_id: str,
             cache: SortieEvaluationCache, mode: str,
             max_existing: int = 10) -> list[tuple[float, tuple[Q2SortieSpec, ...]]]:
    box = scenario.boxes[box_id]
    service = box.service_id
    workload = {model: 0.0 for model in scenario.transport_models}
    for spec in specs:
        workload[spec.model_id] += cache.get(spec).total_operation_s
    drone_count = {model: sum(drone.model_id == model for drone in scenario.transport_drones.values())
                   for model in scenario.transport_models}
    nearest = sorted(range(len(specs)), key=lambda i: (
        0 if service in specs[i].route else min(
            segments.get(service, other).horizontal_distance_m
            for other in specs[i].route[1:-1]), i))[:max_existing]
    choices = []
    for index in [*nearest, None]:
        if index is None:
            original = None
            old_energy = old_duration = 0.0
            base = {}
            order = ()
        else:
            original = specs[index]
            old_eval = cache.get(original)
            old_energy, old_duration = old_eval.total_flight_energy_kwh, old_eval.total_operation_s
            base, order = original.delivery_map(), original.route[1:-1]
        for model in scenario.transport_models:
            if index is not None and model != original.model_id and len(nearest) > 5:
                # Model changes are explored on the closest subset and by the
                # separate v1 change_model neighborhood retained in the driver.
                if index not in nearest[:5]:
                    continue
            positions = [0] if service in order else range(len(order) + 1)
            for position in positions:
                mapping = dict(base)
                mapping[service] = mapping.get(service, ()) + (box_id,)
                new_order = order if service in order else order[:position] + (service,) + order[position:]
                candidate = make_spec(model, mapping, new_order)
                if not cache.feasible(candidate):
                    continue
                evaluation = cache.get(candidate)
                offset = _delivery_offset(evaluation, service)
                if box.material_type == MEDICAL_TYPE and offset > box.desired_delivery_s + 1e-8:
                    continue
                if box.first_batch and offset > box.first_batch_deadline_s + 1e-8:
                    continue
                marginal_duration = evaluation.total_operation_s - old_duration
                marginal_energy = evaluation.total_flight_energy_kwh - old_energy
                load_after = (workload[model] + evaluation.total_operation_s -
                              (old_duration if original and model == original.model_id else 0))
                balance = load_after / max(1, drone_count[model])
                urgency = box.emergency_priority * offset / box.desired_delivery_s
                if mode == "makespan_balance_repair":
                    cost = 0.65 * balance + 0.20 * marginal_duration + 0.10 * offset + 2 * marginal_energy
                elif mode == "deadline_first_repair":
                    cost = 0.45 * offset + 0.20 * balance + 0.12 * marginal_duration + 100 * urgency
                else:
                    cost = 0.22 * offset + 0.25 * balance + 0.15 * marginal_duration + 5 * marginal_energy + 50 * urgency
                if index is None:
                    new_specs = specs + (candidate,)
                else:
                    rows = list(specs)
                    rows[index] = candidate
                    new_specs = tuple(rows)
                choices.append((cost, number(new_specs)))
    choices.sort(key=lambda row: (row[0], len(row[1])))
    return choices


def repair(scenario: Scenario, segments: SegmentMatrix,
           partial: tuple[Q2SortieSpec, ...], removed: tuple[str, ...],
           cache: SortieEvaluationCache, operator: str) -> tuple[Q2SortieSpec, ...] | None:
    if operator not in REPAIR_OPERATORS:
        raise ValueError(f"Unknown repair operator {operator}")
    remaining = list(removed)
    specs = partial
    while remaining:
        if operator == "regret2_insertion":
            sampled = sorted(remaining, key=lambda box_id: (
                scenario.boxes[box_id].desired_delivery_s, box_id))[:8]
            options = [(box_id, _options(scenario, segments, specs, box_id, cache, operator))
                       for box_id in sampled]
            options = [(box_id, choices) for box_id, choices in options if choices]
            if not options:
                return None
            box_id, choices = max(options, key=lambda row: (
                (row[1][1][0] - row[1][0][0]) if len(row[1]) > 1 else inf,
                -row[1][0][0], row[0]))
        else:
            if operator == "deadline_first_repair":
                box_id = min(remaining, key=lambda ident: (
                    min(scenario.boxes[ident].desired_delivery_s,
                        scenario.boxes[ident].first_batch_deadline_s
                        if scenario.boxes[ident].first_batch else inf),
                    -scenario.boxes[ident].emergency_priority, ident))
            else:
                box_id = remaining[0]
            choices = _options(scenario, segments, specs, box_id, cache, operator)
            if not choices:
                return None
        specs = choices[0][1]
        remaining.remove(box_id)
    return specs


def fast_schedule_cached(scenario: Scenario, segments: SegmentMatrix,
                         specs: tuple[Q2SortieSpec, ...],
                         cache: SortieEvaluationCache,
                         rng: random.Random | None = None) -> Q2Solution:
    drones = {ident: 0.0 for ident in scenario.transport_drones}
    batteries = {ident: 0.0 for ident in battery_inventory(scenario)}
    inventory = battery_inventory(scenario)
    items = list(specs)
    if rng:
        rng.shuffle(items)
    items.sort(key=lambda spec: (hard_deadline(scenario, spec),
                                 -max(scenario.boxes[box_id].emergency_priority
                                      for stop in spec.deliveries for box_id in stop.box_ids)))
    assignments = []
    for spec in items:
        evaluation = cache.get(spec)
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
