"""Q2 route, resource and deadline regression tests against real source data."""

from dataclasses import replace
from pathlib import Path

import pytest

from relief_uav.data import load_scenario
from relief_uav.geo import build_segment_matrix
from relief_uav.physics.battery import charge_to_full_s
from relief_uav.physics.sortie import evaluate_transport_sortie
from relief_uav.q2.model import StopAssignment
from relief_uav.q2.report import load_q2_solution
from relief_uav.q2.search import make_spec, objective_key, q1_seed
from relief_uav.q2.timeline import build_solution, delivery_offsets_s
from relief_uav.validation.q2 import validate_q2


@pytest.fixture(scope="module")
def context():
    root = Path(__file__).resolve().parents[1]
    scenario = load_scenario(root)
    segments = build_segment_matrix(scenario)
    saved = load_q2_solution(root / "outputs/q2/q2_summary.json")
    return scenario, segments, saved


def checks(result):
    return {issue.check for issue in result.issues}


def altered(solution, *sorties):
    return replace(solution, sorties=tuple(sorties))


def same_model_pair(solution):
    for first in solution.sorties:
        for second in solution.sorties:
            if first is not second and first.spec.model_id == second.spec.model_id:
                return first, second
    raise AssertionError("Expected at least two sorties for one model")


def test_two_service_delivery_offsets(context):
    scenario, segments, _ = context
    a = next(b.box_id for b in scenario.boxes.values() if b.service_id == "S001")
    b = next(b.box_id for b in scenario.boxes.values() if b.service_id == "S002")
    spec = make_spec("C", {"S001": (a,), "S002": (b,)}, ("S001", "S002"))
    evaluation = evaluate_transport_sortie(scenario, segments, "C", spec.route,
                                            spec.delivery_map())
    offset1, offset2 = delivery_offsets_s(scenario, segments, spec)
    assert offset1 == pytest.approx(evaluation.preparation_s +
                                    evaluation.legs[0].flight_time_s +
                                    evaluation.stops[0].handover_s)
    assert offset2 == pytest.approx(offset1 + evaluation.legs[1].flight_time_s +
                                    evaluation.stops[1].handover_s)
    assert evaluation.legs[1].carried_payload_kg == pytest.approx(scenario.boxes[b].mass_kg)


def test_complete_saved_scenario_passes(context):
    scenario, segments, solution = context
    result = validate_q2(scenario, segments, solution)
    assert result.passed, result.issues[:5]
    assert result.delivered_unique_boxes == 80


def test_q2_seed_does_not_require_saved_q1_file(context, tmp_path):
    scenario, segments, _ = context
    without_saved_q1 = replace(scenario, root=tmp_path)
    specs = q1_seed(without_saved_q1, segments)
    assert len(specs) == 18
    assert {b for s in specs for stop in s.deliveries for b in stop.box_ids} == set(scenario.boxes)


def test_objective_modes_are_explicit(context):
    solution = context[2]
    assert objective_key(solution, "lexicographic") == solution.objective.lex_key
    assert len(objective_key(solution, "weighted")) == 1
    with pytest.raises(ValueError):
        objective_key(solution, "undefined")


def test_drone_overlap_rejected(context):
    scenario, segments, solution = context
    first, second = same_model_pair(solution)
    modified = replace(second, drone_id=first.drone_id,
                       preparation_start_s=first.preparation_start_s)
    rows = tuple(modified if s is second else s for s in solution.sorties)
    assert "drone_overlap" in checks(validate_q2(scenario, segments, altered(solution, *rows)))


def test_battery_overlap_and_unfilled_reuse_rejected(context):
    scenario, segments, solution = context
    first, second = same_model_pair(solution)
    simultaneous = replace(second, battery_id=first.battery_id,
                           preparation_start_s=first.preparation_start_s)
    rows = tuple(simultaneous if s is second else s for s in solution.sorties)
    assert "battery_or_charge_overlap" in checks(
        validate_q2(scenario, segments, altered(solution, *rows)))
    recharge = charge_to_full_s(first.return_soc,
        scenario.battery_stocks[first.spec.model_id].full_charge_s)
    assert recharge > 2
    unfilled = replace(second, battery_id=first.battery_id,
                       preparation_start_s=first.return_o01_time_s + 1)
    rows = tuple(unfilled if s is second else s for s in solution.sorties)
    assert "battery_or_charge_overlap" in checks(
        validate_q2(scenario, segments, altered(solution, *rows)))


def test_wrong_battery_model_and_soc_rejected(context):
    scenario, segments, solution = context
    first = solution.sorties[0]
    wrong_battery = next(s.battery_id for s in solution.sorties
                         if s.spec.model_id != first.spec.model_id)
    rows = tuple(replace(s, battery_id=wrong_battery) if s is first else s
                 for s in solution.sorties)
    assert "battery_model_mismatch" in checks(
        validate_q2(scenario, segments, altered(solution, *rows)))
    models = dict(scenario.transport_models)
    model = models[first.spec.model_id]
    models[model.model_id] = replace(model, usable_energy_kwh=model.usable_energy_kwh * 0.2)
    changed_scenario = replace(scenario, transport_models=models)
    assert "return_soc_below_limit" in checks(
        validate_q2(changed_scenario, segments, solution))


@pytest.mark.parametrize("kind", ["medical", "first_batch"])
def test_hard_deadline_violation_rejected(context, kind):
    scenario, segments, solution = context
    target = next((s for s in solution.sorties
                   if any((scenario.boxes[b].material_type == "医疗物资" if kind == "medical"
                           else scenario.boxes[b].first_batch)
                          for stop in s.spec.deliveries for b in stop.box_ids)))
    late = replace(target, preparation_start_s=20000)
    rows = tuple(late if s is target else s for s in solution.sorties)
    required = "medical_deadline" if kind == "medical" else "first_batch_deadline"
    assert required in checks(validate_q2(scenario, segments, altered(solution, *rows)))


def test_equal_deadline_allowed(context):
    scenario, segments, solution = context
    delivery = next(d for d in solution.box_deliveries
                    if scenario.boxes[d.box_id].first_batch)
    boxes = dict(scenario.boxes)
    boxes[delivery.box_id] = replace(boxes[delivery.box_id],
                                     first_batch_deadline_s=delivery.delivery_complete_time_s)
    modified_scenario = replace(scenario, boxes=boxes)
    assignments = tuple((s.spec, s.drone_id, s.battery_id, s.preparation_start_s)
                        for s in solution.sorties)
    rebuilt = build_solution(modified_scenario, segments, assignments)
    assert "first_batch_deadline" not in checks(
        validate_q2(modified_scenario, segments, rebuilt))


def test_parallel_resources_and_battery_swap(context):
    scenario, segments, solution = context
    first, second = next((a, b) for a in solution.sorties for b in solution.sorties
                         if a.spec.model_id == b.spec.model_id and
                         a.drone_id != b.drone_id and a.battery_id != b.battery_id)
    parallel = build_solution(scenario, segments,
        ((first.spec, first.drone_id, first.battery_id, 0.0),
         (second.spec, second.drone_id, second.battery_id, 0.0)))
    issue_set = checks(validate_q2(scenario, segments, parallel))
    assert "drone_overlap" not in issue_set
    assert "battery_or_charge_overlap" not in issue_set
    swap = build_solution(scenario, segments,
        ((first.spec, first.drone_id, first.battery_id, 0.0),
         (second.spec, first.drone_id, second.battery_id,
          first.return_o01_time_s - first.preparation_start_s)))
    issue_set = checks(validate_q2(scenario, segments, swap))
    assert "drone_overlap" not in issue_set
    assert "battery_or_charge_overlap" not in issue_set


def test_duplicate_and_missing_box_rejected(context):
    scenario, segments, solution = context
    first = solution.sorties[0]
    stop = first.spec.deliveries[0]
    duplicate = replace(stop, box_ids=stop.box_ids + (stop.box_ids[0],))
    spec = replace(first.spec, deliveries=(duplicate,) + first.spec.deliveries[1:])
    rows = tuple(replace(s, spec=spec) if s is first else s for s in solution.sorties)
    assert "duplicate_box" in checks(validate_q2(scenario, segments, altered(solution, *rows)))
    missing = tuple(s for s in solution.sorties if s is not first)
    assert "missing_box" in checks(validate_q2(scenario, segments, altered(solution, *missing)))
