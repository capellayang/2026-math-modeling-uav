"""Independent Q2 replay from source boxes, routes, model, resources and starts."""

from collections import Counter, defaultdict
from dataclasses import dataclass, fields, is_dataclass
from math import isclose, isfinite

from relief_uav.data.models import Scenario
from relief_uav.geo.segments import SegmentMatrix
from relief_uav.physics.sortie import SortieInputError, evaluate_transport_sortie
from relief_uav.q2.model import Q2Solution
from relief_uav.q2.timeline import MEDICAL_TYPE, battery_inventory, build_solution


@dataclass(frozen=True)
class Q2Issue:
    scope: str
    check: str
    detail: str


@dataclass(frozen=True)
class Q2Validation:
    passed: bool
    expected_boxes: int
    delivered_unique_boxes: int
    checked_sorties: int
    issues: tuple[Q2Issue, ...]


def _compare(saved, actual, path: str, add) -> None:
    if is_dataclass(actual):
        if not is_dataclass(saved):
            add(path, "saved_value_mismatch", "expected dataclass")
            return
        for field in fields(actual):
            _compare(getattr(saved, field.name), getattr(actual, field.name),
                     f"{path}.{field.name}", add)
    elif isinstance(actual, (tuple, list)):
        if not isinstance(saved, (tuple, list)) or len(saved) != len(actual):
            add(path, "saved_value_mismatch", "sequence length differs")
            return
        for index, (x, y) in enumerate(zip(saved, actual)):
            _compare(x, y, f"{path}[{index}]", add)
    elif isinstance(actual, (int, float)) and not isinstance(actual, bool):
        if not isinstance(saved, (int, float)) or not isfinite(saved) or not isclose(
                saved, actual, rel_tol=1e-9, abs_tol=1e-6):
            add(path, "saved_value_mismatch", f"saved={saved!r}, rebuilt={actual!r}")
    elif saved != actual:
        add(path, "saved_value_mismatch", f"saved={saved!r}, rebuilt={actual!r}")


def validate_q2(scenario: Scenario, segments: SegmentMatrix,
                solution: Q2Solution) -> Q2Validation:
    issues = []

    def add(scope: str, check: str, detail: str):
        issues.append(Q2Issue(scope, check, detail))

    inventory = battery_inventory(scenario)
    counts = Counter(box_id for sortie in solution.sorties
                     for stop in sortie.spec.deliveries for box_id in stop.box_ids)
    for box_id in sorted(set(scenario.boxes) - set(counts)):
        add("GLOBAL", "missing_box", box_id)
    for box_id, count in sorted(counts.items()):
        if box_id not in scenario.boxes:
            add("GLOBAL", "unknown_box", box_id)
        elif count != 1:
            add("GLOBAL", "duplicate_box", f"{box_id}: {count}")
    ids = [sortie.spec.sortie_id for sortie in solution.sorties]
    if len(ids) != len(set(ids)):
        add("GLOBAL", "duplicate_sortie_id", "Sortie IDs must be unique")
    replay_inputs = []
    for sortie in solution.sorties:
        spec = sortie.spec
        scope = spec.sortie_id
        if not isfinite(sortie.preparation_start_s) or sortie.preparation_start_s < 0:
            add(scope, "invalid_start", str(sortie.preparation_start_s))
            continue
        drone = scenario.transport_drones.get(sortie.drone_id)
        if drone is None or drone.model_id != spec.model_id:
            add(scope, "drone_model_mismatch", f"{sortie.drone_id} / {spec.model_id}")
        if inventory.get(sortie.battery_id) != spec.model_id:
            add(scope, "battery_model_mismatch", f"{sortie.battery_id} / {spec.model_id}")
        for stop in spec.deliveries:
            for box_id in stop.box_ids:
                box = scenario.boxes.get(box_id)
                if box is not None and box.service_id != stop.service_id:
                    add(scope, "wrong_service", f"{box_id} belongs to {box.service_id}")
        try:
            evaluation = evaluate_transport_sortie(
                scenario, segments, spec.model_id, spec.route, spec.delivery_map())
        except (SortieInputError, ValueError, KeyError) as exc:
            add(scope, "sortie_input", str(exc))
            continue
        if not evaluation.mass_feasible:
            add(scope, "overweight", str(evaluation.loaded_mass_kg))
        if not evaluation.volume_feasible:
            add(scope, "overvolume", str(evaluation.loaded_volume_m3))
        if not evaluation.energy_feasible:
            add(scope, "return_soc_below_limit", str(evaluation.return_soc))
        replay_inputs.append((spec, sortie.drone_id, sortie.battery_id,
                              sortie.preparation_start_s))
    if len(replay_inputs) != len(solution.sorties):
        return Q2Validation(False, len(scenario.boxes), len(set(counts) & set(scenario.boxes)),
                            len(solution.sorties), tuple(issues))
    try:
        rebuilt = build_solution(scenario, segments, tuple(replay_inputs),
                                 seed=solution.seed, objective_mode=solution.objective_mode,
                                 search_seconds=solution.search_seconds,
                                 pareto_count=solution.pareto_count)
    except (SortieInputError, ValueError, KeyError) as exc:
        add("GLOBAL", "replay_failure", str(exc))
        return Q2Validation(False, len(scenario.boxes), len(set(counts) & set(scenario.boxes)),
                            len(solution.sorties), tuple(issues))
    for saved, actual in zip(solution.sorties, rebuilt.sorties):
        _compare(saved, actual, actual.spec.sortie_id, add)
    for name in ("box_deliveries", "battery_uses", "charge_events", "objective"):
        _compare(getattr(solution, name), getattr(rebuilt, name), name, add)
    by_drone = defaultdict(list)
    by_battery = defaultdict(list)
    for sortie, charge in zip(rebuilt.sorties, rebuilt.charge_events):
        by_drone[sortie.drone_id].append((sortie.preparation_start_s,
                                         sortie.return_o01_time_s, sortie.spec.sortie_id))
        by_battery[sortie.battery_id].append((sortie.preparation_start_s,
                                             charge.charge_end_s, sortie.spec.sortie_id))
    for kind, groups in (("drone_overlap", by_drone), ("battery_or_charge_overlap", by_battery)):
        for resource, intervals in groups.items():
            intervals.sort()
            for earlier, later in zip(intervals, intervals[1:]):
                if later[0] < earlier[1] - 1e-7:
                    add(resource, kind, f"{earlier[2]} ends {earlier[1]:.6f}; {later[2]} starts {later[0]:.6f}")
    for delivery in rebuilt.box_deliveries:
        box = scenario.boxes[delivery.box_id]
        if box.material_type == MEDICAL_TYPE and delivery.delivery_complete_time_s > box.desired_delivery_s + 1e-7:
            add(box.box_id, "medical_deadline", f"{delivery.delivery_complete_time_s:.6f} > {box.desired_delivery_s:.6f}")
        if box.first_batch and delivery.delivery_complete_time_s > box.first_batch_deadline_s + 1e-7:
            add(box.box_id, "first_batch_deadline", f"{delivery.delivery_complete_time_s:.6f} > {box.first_batch_deadline_s:.6f}")
    if len(rebuilt.box_deliveries) != len(scenario.boxes):
        add("GLOBAL", "delivery_count", str(len(rebuilt.box_deliveries)))
    return Q2Validation(not issues, len(scenario.boxes), len(set(counts) & set(scenario.boxes)),
                        len(solution.sorties), tuple(issues))
