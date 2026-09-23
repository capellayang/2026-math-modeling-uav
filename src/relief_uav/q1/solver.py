"""Exact per-service subset enumeration and bitmask set-partition DP."""

from dataclasses import dataclass
from functools import lru_cache
from math import fsum

from relief_uav.data.models import Scenario
from relief_uav.geo.segments import SegmentMatrix
from relief_uav.physics.sortie import evaluate_transport_sortie
from .model import Q1Solution, Q1Trip, SafePayload


class Q1InfeasibleError(ValueError):
    pass


@dataclass(frozen=True)
class _Candidate:
    mask: int
    model_id: str
    box_ids: tuple[str, ...]
    mass_kg: float
    volume_m3: float
    energy_kwh: float
    operation_s: float


def _continuous_round_trip_energy(scenario: Scenario, segments: SegmentMatrix,
                                  model_id: str, service_id: str, payload_kg: float):
    """Continuous payload is not a box set; both legs use common energy model."""
    from relief_uav.physics.energy import transport_segment_energy

    model = scenario.transport_models[model_id]
    outward = transport_segment_energy(model, segments.get("O01", service_id), payload_kg)
    returning = transport_segment_energy(model, segments.get(service_id, "O01"), 0.0)
    energy = outward.total_kwh + returning.total_kwh
    return energy, 1.0 - energy / model.usable_energy_kwh


def safe_payload_matrix(scenario: Scenario, segments: SegmentMatrix,
                        safety_margin_soc: float = 0.20, precision_kg: float = 1e-4
                        ) -> tuple[SafePayload, ...]:
    if not 0 <= safety_margin_soc <= 1 or precision_kg <= 0:
        raise ValueError("Invalid SOC safety margin or mass precision")
    rows = []
    for service_id in sorted(scenario.services):
        outward = segments.get("O01", service_id)
        for model_id in sorted(scenario.transport_models):
            model = scenario.transport_models[model_id]
            allowed = (1.0 - safety_margin_soc) * model.usable_energy_kwh
            empty_energy, _ = _continuous_round_trip_energy(scenario, segments, model_id,
                                                             service_id, 0.0)
            if empty_energy > allowed + 1e-10:
                rows.append(SafePayload(service_id, model_id, outward.horizontal_distance_m,
                                        outward.maximum_dem_m, outward.cruise_altitude_m,
                                        model.max_payload_kg, None, None, None, None))
                continue
            full_energy, _ = _continuous_round_trip_energy(scenario, segments, model_id,
                                                            service_id, model.max_payload_kg)
            if full_energy <= allowed + 1e-10:
                payload = model.max_payload_kg
                energy = full_energy
                limited = False
            else:
                lower, upper = 0.0, model.max_payload_kg
                while upper - lower > precision_kg:
                    middle = (lower + upper) / 2
                    e, _ = _continuous_round_trip_energy(scenario, segments, model_id,
                                                          service_id, middle)
                    if e <= allowed:
                        lower = middle
                    else:
                        upper = middle
                payload = lower  # Feasible lower bound; never round upward.
                energy, _ = _continuous_round_trip_energy(scenario, segments, model_id,
                                                          service_id, payload)
                limited = True
            rows.append(SafePayload(service_id, model_id, outward.horizontal_distance_m,
                                    outward.maximum_dem_m, outward.cruise_altitude_m,
                                    model.max_payload_kg, payload, energy,
                                    1.0 - energy / model.usable_energy_kwh, limited))
    return tuple(rows)


def _service_candidates(scenario: Scenario, segments: SegmentMatrix, service_id: str,
                        safety_margin_soc: float) -> tuple[list[str], dict[int, _Candidate]]:
    boxes = sorted((b for b in scenario.boxes.values() if b.service_id == service_id),
                   key=lambda b: b.box_id)
    ids = [b.box_id for b in boxes]
    n = len(boxes)
    sizes = 1 << n
    masses = [0.0] * sizes
    volumes = [0.0] * sizes
    subsets = [()] * sizes
    candidates = {}
    for mask in range(1, sizes):
        lowbit = mask & -mask
        index = lowbit.bit_length() - 1
        previous = mask ^ lowbit
        masses[mask] = masses[previous] + boxes[index].mass_kg
        volumes[mask] = volumes[previous] + boxes[index].volume_m3
        subsets[mask] = subsets[previous] + (boxes[index].box_id,)
        mass, volume, box_ids = masses[mask], volumes[mask], subsets[mask]
        best = None
        for model_id in sorted(scenario.transport_models):
            model = scenario.transport_models[model_id]
            if mass > model.max_payload_kg + 1e-9 or volume > model.max_volume_m3 + 1e-9:
                continue
            # The continuous matrix is an admissible prefilter; every retained
            # actual combination still passes through the complete evaluator.
            evaluation = evaluate_transport_sortie(
                scenario, segments, model_id, ("O01", service_id, "O01"),
                {service_id: box_ids}, safety_margin_soc=safety_margin_soc)
            if not evaluation.feasible:
                continue
            candidate = _Candidate(mask, model_id, box_ids, mass, volume,
                                   evaluation.total_flight_energy_kwh,
                                   evaluation.total_operation_s)
            # Same box subset always costs one trip. Only its lexicographically
            # cheapest feasible model can be part of a global lex optimum.
            if best is None or (candidate.energy_kwh, candidate.operation_s, model_id) < (
                    best.energy_kwh, best.operation_s, best.model_id):
                best = candidate
        if best is not None:
            candidates[mask] = best
    return ids, candidates


def _solve_service(scenario: Scenario, segments: SegmentMatrix, service_id: str,
                   safety_margin_soc: float, first_trip_number: int) -> list[Q1Trip]:
    ids, candidates = _service_candidates(scenario, segments, service_id,
                                           safety_margin_soc)
    if not ids:
        return []
    n = len(ids)
    by_first = [[] for _ in range(n)]
    for c in candidates.values():
        for index in range(n):
            if c.mask & (1 << index):
                by_first[index].append(c)

    @lru_cache(None)
    def solve(uncovered: int):
        if uncovered == 0:
            return (0, 0.0, 0.0, ())
        anchor = (uncovered & -uncovered).bit_length() - 1
        best = None
        for c in by_first[anchor]:
            if c.mask & uncovered != c.mask:
                continue
            tail = solve(uncovered ^ c.mask)
            if tail is None:
                continue
            proposal = (1 + tail[0], c.energy_kwh + tail[1],
                        c.operation_s + tail[2], (c.mask,) + tail[3])
            if best is None or proposal[:3] < best[:3]:
                best = proposal
        return best

    optimum = solve((1 << n) - 1)
    if optimum is None:
        raise Q1InfeasibleError(f"No feasible single-service cover for {service_id} at {safety_margin_soc:.0%}")
    trips = []
    for offset, mask in enumerate(optimum[3]):
        candidate = candidates[mask]
        evaluation = evaluate_transport_sortie(
            scenario, segments, candidate.model_id, ("O01", service_id, "O01"),
            {service_id: candidate.box_ids}, safety_margin_soc=safety_margin_soc)
        outward, returning = evaluation.legs
        trips.append(Q1Trip(f"Q1-{first_trip_number + offset:03d}", service_id,
                            candidate.model_id, candidate.box_ids, evaluation.loaded_mass_kg,
                            evaluation.loaded_volume_m3, outward.carried_payload_kg,
                            outward.total_energy_kwh, returning.total_energy_kwh,
                            evaluation.total_flight_energy_kwh, evaluation.preparation_s,
                            outward.flight_time_s, evaluation.total_handover_s,
                            returning.flight_time_s, evaluation.total_operation_s,
                            evaluation.return_soc))
    return trips


def solve_q1(scenario: Scenario, segments: SegmentMatrix, safety_margin_soc: float = 0.20,
             *, objective_mode: str = "lexicographic") -> Q1Solution:
    if objective_mode != "lexicographic":
        raise NotImplementedError("Q1 currently implements only lexicographic mode; weighted/pareto are reserved")
    safe = safe_payload_matrix(scenario, segments, safety_margin_soc)
    trips = []
    for service_id in sorted(scenario.services):
        service_trips = _solve_service(scenario, segments, service_id,
                                       safety_margin_soc, len(trips) + 1)
        trips.extend(service_trips)
    return Q1Solution(safety_margin_soc, objective_mode, safe, tuple(trips), len(trips),
                      fsum(t.total_energy_kwh for t in trips),
                      fsum(t.operation_s for t in trips))
