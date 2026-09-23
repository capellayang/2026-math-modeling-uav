from dataclasses import replace
from itertools import combinations
from math import fsum

import pytest

from relief_uav.data import load_scenario
from relief_uav.geo import build_segment_matrix
from relief_uav.geo.segments import DirectedSegment
from relief_uav.physics import evaluate_transport_sortie
from relief_uav.physics.energy import transport_segment_energy
from relief_uav.q1 import safe_payload_matrix, solve_q1
from relief_uav.validation import validate_q1


@pytest.fixture(scope="module")
def real_case():
    scenario = load_scenario()
    matrix = build_segment_matrix(scenario)
    solution = solve_q1(scenario, matrix)
    return scenario, matrix, solution


def test_energy_payload_and_zero_phase_boundaries(real_case):
    scenario, matrix, _ = real_case
    segment = matrix.get("O01", "S001")
    for model in scenario.transport_models.values():
        levels = (0, model.max_payload_kg / 2, model.max_payload_kg)
        results = [transport_segment_energy(model, segment, q) for q in levels]
        assert results[0].equivalent_range_m == model.empty_range_m
        assert results[-1].equivalent_range_m == pytest.approx(model.full_range_m)
        assert results[0].total_kwh <= results[1].total_kwh <= results[2].total_kwh
        assert results[0].equivalent_range_m >= results[1].equivalent_range_m >= results[2].equivalent_range_m
        zero_distance = replace(segment, horizontal_distance_m=0)
        zero_climb = replace(segment, climb_m=0)
        assert transport_segment_energy(model, zero_distance, 0).horizontal_kwh == 0
        assert transport_segment_energy(model, zero_climb, 0).climb_additional_kwh == 0
        assert transport_segment_energy(model, segment, 0).total_kwh > 0


def test_real_single_point_energy_at_three_payload_levels(real_case):
    scenario, matrix, _ = real_case
    for model in scenario.transport_models.values():
        outward = matrix.get("O01", "S001")
        returning = matrix.get("S001", "O01")
        empty_return = transport_segment_energy(model, returning, 0).total_kwh
        total = []
        for q in (0, model.max_payload_kg / 2, model.max_payload_kg):
            result = transport_segment_energy(model, outward, q)
            total.append(result.total_kwh + empty_return)
        assert total[0] < total[1] < total[2]


def test_multi_point_sortie_reduces_payload_after_delivery(real_case):
    scenario, matrix, _ = real_case
    first = next(b.box_id for b in scenario.boxes.values() if b.service_id == "S001" and b.mass_kg == 3)
    second = next(b.box_id for b in scenario.boxes.values() if b.service_id == "S005" and b.mass_kg == 3)
    result = evaluate_transport_sortie(scenario, matrix, "A",
                                       ("O01", "S001", "S005", "O01"),
                                       {"S001": (first,), "S005": (second,)})
    assert [leg.carried_payload_kg for leg in result.legs] == [6, 3, 0]
    assert result.preparation_s == 360
    assert len(result.stops) == 2
    assert result.total_operation_s == pytest.approx(
        result.preparation_s + result.total_handover_s + result.total_flight_time_s)


def test_45_safe_payloads_are_feasible_and_maximal(real_case):
    scenario, matrix, solution = real_case
    assert len(solution.safe_payloads) == 45
    for row in solution.safe_payloads:
        model = scenario.transport_models[row.model_id]
        assert row.safe_payload_kg is not None
        assert 0 <= row.safe_payload_kg <= model.max_payload_kg
        outward = transport_segment_energy(model, matrix.get("O01", row.service_id), row.safe_payload_kg)
        returning = transport_segment_energy(model, matrix.get(row.service_id, "O01"), 0)
        energy = outward.total_kwh + returning.total_kwh
        assert energy <= 0.8 * model.usable_energy_kwh + 1e-8
        assert row.sortie_energy_kwh == pytest.approx(energy)
        if row.energy_limited:
            probe = min(model.max_payload_kg, row.safe_payload_kg + 0.002)
            e_probe = transport_segment_energy(model, matrix.get("O01", row.service_id), probe).total_kwh + returning.total_kwh
            assert e_probe > 0.8 * model.usable_energy_kwh
        else:
            assert row.safe_payload_kg == model.max_payload_kg


def test_real_grouping_and_independent_validator(real_case):
    scenario, matrix, solution = real_case
    report = validate_q1(scenario, matrix, solution)
    assert report.passed, report.issues
    assert len(solution.trips) == solution.total_trips
    assert {t.service_id for t in solution.trips} == set(scenario.services)
    assert fsum(t.total_energy_kwh for t in solution.trips) == pytest.approx(solution.total_energy_kwh)
    assert fsum(t.operation_s for t in solution.trips) == pytest.approx(solution.total_operation_s)


def test_validator_detects_duplicate_missing_overweight_overvolume_and_soc(real_case):
    scenario, matrix, solution = real_case
    first = solution.trips[0]
    duplicate = replace(first, box_ids=first.box_ids + (first.box_ids[0],))
    report = validate_q1(scenario, matrix, replace(solution, trips=(duplicate,) + solution.trips[1:]))
    assert "duplicate_box" in {i.check for i in report.issues}
    missing = replace(solution, trips=solution.trips[1:])
    assert "missing_box" in {i.check for i in validate_q1(scenario, matrix, missing).issues}
    s001 = [b.box_id for b in scenario.boxes.values() if b.service_id == "S001"]
    overweight = replace(first, service_id="S001", model_id="A", box_ids=tuple(s001))
    assert "overweight" in {i.check for i in validate_q1(scenario, matrix,
        replace(solution, trips=(overweight,) + solution.trips[1:])).issues}
    hygiene = [b.box_id for b in scenario.boxes.values()
               if b.service_id == "S001" and b.material_type == "生活卫生用品"]
    overvolume = replace(first, service_id="S001", model_id="A", box_ids=tuple(hygiene))
    assert "overvolume" in {i.check for i in validate_q1(scenario, matrix,
        replace(solution, trips=(overvolume,) + solution.trips[1:])).issues}
    assert "return_soc_below_limit" in {i.check for i in validate_q1(
        scenario, matrix, solution, safety_margin_soc=0.99).issues}


def test_small_service_dp_matches_independent_partition_enumeration(real_case):
    scenario, matrix, _ = real_case
    boxes = sorted([b for b in scenario.boxes.values() if b.service_id == "S001"], key=lambda b: b.box_id)[:4]
    small = replace(scenario, services={"S001": scenario.services["S001"]},
                    boxes={b.box_id: b for b in boxes})
    solved = solve_q1(small, matrix)
    ids = tuple(b.box_id for b in boxes)
    costs = {}
    for size in range(1, len(ids) + 1):
        for subset in combinations(ids, size):
            for model_id in small.transport_models:
                model = small.transport_models[model_id]
                if sum(small.boxes[x].mass_kg for x in subset) > model.max_payload_kg:
                    continue
                r = evaluate_transport_sortie(small, matrix, model_id,
                                              ("O01", "S001", "O01"), {"S001": subset})
                if r.feasible:
                    costs[(subset, model_id)] = (1, r.total_flight_energy_kwh, r.total_operation_s)

    def independent_cover(remaining):
        if not remaining:
            return (0, 0.0, 0.0)
        anchor = min(remaining)
        best = None
        for (subset, model_id), cost in costs.items():
            ss = set(subset)
            if anchor not in ss or not ss <= remaining:
                continue
            tail = independent_cover(remaining - ss)
            proposal = tuple(cost[i] + tail[i] for i in range(3))
            if best is None or proposal < best:
                best = proposal
        return best

    brute = independent_cover(set(ids))
    assert solved.total_trips == brute[0]
    assert solved.total_energy_kwh == pytest.approx(brute[1])
    assert solved.total_operation_s == pytest.approx(brute[2])
