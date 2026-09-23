"""Independent Q1 validation by rebuilding sorties from source box IDs."""

from collections import Counter
from dataclasses import dataclass
from math import fsum, isclose

from relief_uav.data.models import Scenario
from relief_uav.geo.segments import SegmentMatrix
from relief_uav.physics.sortie import SortieInputError, evaluate_transport_sortie
from relief_uav.q1.model import Q1Solution


@dataclass(frozen=True)
class Q1Issue:
    trip_id: str
    check: str
    detail: str


@dataclass(frozen=True)
class Q1Validation:
    passed: bool
    expected_boxes: int
    delivered_unique_boxes: int
    checked_trips: int
    issues: tuple[Q1Issue, ...]


def _same(actual: float, saved: float) -> bool:
    return isclose(actual, saved, rel_tol=1e-9, abs_tol=1e-7)


def validate_q1(scenario: Scenario, segments: SegmentMatrix, solution: Q1Solution,
                *, safety_margin_soc: float | None = None) -> Q1Validation:
    """Use source data and the common physics functions, never solver candidate costs."""
    margin = solution.safety_margin_soc if safety_margin_soc is None else safety_margin_soc
    issues = []

    def add(trip_id: str, check: str, detail: str):
        issues.append(Q1Issue(trip_id, check, detail))

    counts = Counter(box_id for trip in solution.trips for box_id in trip.box_ids)
    expected = set(scenario.boxes)
    for box_id in sorted(expected - set(counts)):
        add("GLOBAL", "missing_box", box_id)
    for box_id, count in sorted(counts.items()):
        if box_id not in expected:
            add("GLOBAL", "unknown_box", box_id)
        elif count != 1:
            add("GLOBAL", "duplicate_box", f"{box_id}: {count} times")
    if len({t.trip_id for t in solution.trips}) != len(solution.trips):
        add("GLOBAL", "duplicate_trip_id", "Trip IDs must be unique")
    for trip in solution.trips:
        if trip.service_id not in scenario.services:
            add(trip.trip_id, "unknown_service", trip.service_id)
            continue
        if trip.model_id not in scenario.transport_models:
            add(trip.trip_id, "unknown_model", trip.model_id)
            continue
        if not trip.box_ids:
            add(trip.trip_id, "empty_trip", "No boxes")
            continue
        own = []
        for box_id in trip.box_ids:
            box = scenario.boxes.get(box_id)
            if box is None:
                continue
            if box.service_id != trip.service_id:
                add(trip.trip_id, "cross_service", f"{box_id} belongs to {box.service_id}")
            else:
                own.append(box)
        model = scenario.transport_models[trip.model_id]
        mass = fsum(box.mass_kg for box in own)
        volume = fsum(box.volume_m3 for box in own)
        if mass > model.max_payload_kg + 1e-9:
            add(trip.trip_id, "overweight", f"{mass:g} > {model.max_payload_kg:g} kg")
        if volume > model.max_volume_m3 + 1e-9:
            add(trip.trip_id, "overvolume", f"{volume:g} > {model.max_volume_m3:g} m³")
        if any(box_id not in scenario.boxes or scenario.boxes[box_id].service_id != trip.service_id
               for box_id in trip.box_ids):
            continue
        try:
            evaluation = evaluate_transport_sortie(
                scenario, segments, trip.model_id, ("O01", trip.service_id, "O01"),
                {trip.service_id: trip.box_ids}, safety_margin_soc=margin)
        except SortieInputError as exc:
            add(trip.trip_id, "sortie_input", str(exc))
            continue
        if not evaluation.volume_feasible:
            add(trip.trip_id, "overvolume", "Actual sortie volume violates model capacity")
        if not evaluation.energy_feasible:
            add(trip.trip_id, "return_soc_below_limit",
                f"computed SOC={evaluation.return_soc:.9f}, required={margin:.9f}")
        outbound, returning = evaluation.legs
        expected_values = {
            "mass_kg": evaluation.loaded_mass_kg,
            "volume_m3": evaluation.loaded_volume_m3,
            "outgoing_payload_kg": outbound.carried_payload_kg,
            "outgoing_energy_kwh": outbound.total_energy_kwh,
            "returning_energy_kwh": returning.total_energy_kwh,
            "total_energy_kwh": evaluation.total_flight_energy_kwh,
            "preparation_s": evaluation.preparation_s,
            "outgoing_flight_s": outbound.flight_time_s,
            "handover_s": evaluation.total_handover_s,
            "returning_flight_s": returning.flight_time_s,
            "operation_s": evaluation.total_operation_s,
            "return_soc": evaluation.return_soc,
        }
        for field, recomputed in expected_values.items():
            if not _same(recomputed, getattr(trip, field)):
                add(trip.trip_id, "saved_value_mismatch",
                    f"{field}: saved={getattr(trip, field)!r}, recomputed={recomputed!r}")
    if solution.total_trips != len(solution.trips):
        add("GLOBAL", "trip_total_mismatch", f"{solution.total_trips} != {len(solution.trips)}")
    if not _same(solution.total_energy_kwh, fsum(t.total_energy_kwh for t in solution.trips)):
        add("GLOBAL", "energy_total_mismatch", "Summary differs from trip detail")
    if not _same(solution.total_operation_s, fsum(t.operation_s for t in solution.trips)):
        add("GLOBAL", "time_total_mismatch", "Summary differs from trip detail")
    if solution.objective_mode != "lexicographic":
        add("GLOBAL", "unsupported_objective", solution.objective_mode)
    return Q1Validation(not issues, len(expected), len(set(counts) & expected),
                        len(solution.trips), tuple(issues))
