"""Build complete floating-point Q2 events from the common sortie evaluator."""

from collections import Counter
from math import fsum

from relief_uav.data.models import Scenario
from relief_uav.geo.segments import SegmentMatrix
from relief_uav.physics.battery import charge_to_full_s, remaining_soc
from relief_uav.physics.sortie import evaluate_transport_sortie
from .model import (
    Q2BatteryUse, Q2BoxDelivery, Q2ChargeEvent, Q2LegTimeline, Q2Objective,
    Q2Solution, Q2Sortie, Q2SortieSpec, Q2StopTimeline,
)

MEDICAL_TYPE = "医疗物资"  # Exact category in the original box workbook.


def battery_inventory(scenario: Scenario) -> dict[str, str]:
    """Deterministic project-internal IDs; source workbook gives counts only."""
    return {f"{model_id}-BAT-{index:02d}": model_id
            for model_id, stock in sorted(scenario.battery_stocks.items())
            for index in range(1, stock.count + 1)}


def delivery_offsets_s(scenario: Scenario, segments: SegmentMatrix,
                       spec: Q2SortieSpec) -> tuple[float, ...]:
    evaluation = evaluate_transport_sortie(
        scenario, segments, spec.model_id, spec.route, spec.delivery_map())
    clock = evaluation.preparation_s
    offsets = []
    for index, leg in enumerate(evaluation.legs):
        clock += leg.flight_time_s
        if index < len(evaluation.stops):
            clock += evaluation.stops[index].handover_s
            offsets.append(clock)
    return tuple(offsets)


def construct_sortie(scenario: Scenario, segments: SegmentMatrix,
                     spec: Q2SortieSpec, drone_id: str, battery_id: str,
                     preparation_start_s: float) -> Q2Sortie:
    evaluation = evaluate_transport_sortie(
        scenario, segments, spec.model_id, spec.route, spec.delivery_map())
    if not evaluation.feasible:
        raise ValueError(f"Infeasible physical sortie {spec.sortie_id}")
    clock = preparation_start_s + evaluation.preparation_s
    takeoff = clock
    legs, stops = [], []
    for index, leg in enumerate(evaluation.legs):
        geom = segments.get(leg.start_id, leg.end_id)
        climb_start = clock
        climb_end = climb_start + leg.climb_time_s
        cruise_start = climb_end
        cruise_end = cruise_start + leg.cruise_time_s
        descent_start = cruise_end
        descent_end = descent_start + leg.descent_time_s
        legs.append(Q2LegTimeline(
            leg.start_id, leg.end_id, leg.carried_payload_kg,
            geom.horizontal_distance_m, geom.maximum_dem_m, geom.cruise_altitude_m,
            geom.climb_m, geom.descent_m, leg.equivalent_range_m,
            leg.horizontal_energy_kwh, leg.climb_energy_kwh,
            climb_start, climb_end, cruise_start, cruise_end,
            descent_start, descent_end))
        clock = descent_end
        if index < len(evaluation.stops):
            stop = evaluation.stops[index]
            handover_end = clock + stop.handover_s
            stops.append(Q2StopTimeline(stop.service_id, stop.box_ids, clock,
                                        clock, handover_end, handover_end))
            clock = handover_end
    return Q2Sortie(spec, drone_id, battery_id, preparation_start_s, takeoff,
                    tuple(legs), tuple(stops), clock, evaluation.loaded_mass_kg,
                    evaluation.loaded_volume_m3, evaluation.total_flight_energy_kwh,
                    remaining_soc(scenario.transport_models[spec.model_id].usable_energy_kwh,
                                  evaluation.total_flight_energy_kwh))


def build_solution(scenario: Scenario, segments: SegmentMatrix,
                   assignments: tuple[tuple[Q2SortieSpec, str, str, float], ...],
                   *, seed: int = 20260923, objective_mode: str = "lexicographic",
                   search_seconds: float = 0.0, pareto_count: int = 0) -> Q2Solution:
    sorties = tuple(construct_sortie(scenario, segments, *row) for row in assignments)
    deliveries, uses, charges = [], [], []
    for sortie in sorties:
        for stop in sortie.stops:
            for box_id in stop.box_ids:
                box = scenario.boxes[box_id]
                tardiness = box.emergency_priority * max(
                    0.0, stop.delivery_complete_time_s - box.desired_delivery_s)
                deliveries.append(Q2BoxDelivery(
                    box_id, sortie.spec.sortie_id, stop.service_id,
                    stop.delivery_complete_time_s, box.desired_delivery_s,
                    box.first_batch_deadline_s, box.emergency_priority, tardiness))
        uses.append(Q2BatteryUse(sortie.battery_id, sortie.spec.model_id,
                                 sortie.spec.sortie_id, sortie.preparation_start_s,
                                 sortie.return_o01_time_s, 1.0, sortie.return_soc))
        charge_end = sortie.return_o01_time_s + charge_to_full_s(
            sortie.return_soc, scenario.battery_stocks[sortie.spec.model_id].full_charge_s)
        charges.append(Q2ChargeEvent(sortie.battery_id, sortie.spec.model_id,
                                     sortie.spec.sortie_id, sortie.return_o01_time_s,
                                     charge_end, sortie.return_soc))
    priority_sum = fsum(box.emergency_priority for box in scenario.boxes.values())
    normalized = fsum(scenario.boxes[d.box_id].emergency_priority * max(
        0.0, (d.delivery_complete_time_s - d.desired_delivery_s) / d.desired_delivery_s)
                      for d in deliveries) / priority_sum
    objective = Q2Objective(fsum(d.weighted_tardiness for d in deliveries), normalized,
                            max((s.return_o01_time_s for s in sorties), default=0.0),
                            fsum(s.energy_kwh for s in sorties), len(sorties))
    return Q2Solution(objective_mode, seed, sorties, tuple(deliveries),
                      tuple(uses), tuple(charges), objective, search_seconds,
                      pareto_count)
