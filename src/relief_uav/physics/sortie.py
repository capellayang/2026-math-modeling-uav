"""Reusable transport-sortie evaluator for Q1 and later route scheduling."""

from dataclasses import dataclass
from math import fsum, isfinite
from typing import Mapping, Sequence

from relief_uav.data.models import Scenario
from relief_uav.geo.segments import SegmentMatrix
from .energy import DEFAULT_ENERGY_MODEL, TransportEnergyModel, transport_segment_energy
from .flight import flight_phases, handover_time_s, preparation_time_s


class SortieInputError(ValueError):
    pass


@dataclass(frozen=True)
class SortieLeg:
    start_id: str
    end_id: str
    carried_payload_kg: float
    horizontal_distance_m: float
    equivalent_range_m: float
    horizontal_energy_kwh: float
    climb_energy_kwh: float
    total_energy_kwh: float
    climb_time_s: float
    cruise_time_s: float
    descent_time_s: float

    @property
    def flight_time_s(self) -> float:
        return self.climb_time_s + self.cruise_time_s + self.descent_time_s


@dataclass(frozen=True)
class SortieStop:
    service_id: str
    box_ids: tuple[str, ...]
    delivered_mass_kg: float
    handover_s: float


@dataclass(frozen=True)
class SortieEvaluation:
    route: tuple[str, ...]
    model_id: str
    box_ids: tuple[str, ...]
    loaded_mass_kg: float
    loaded_volume_m3: float
    preparation_s: float
    stops: tuple[SortieStop, ...]
    legs: tuple[SortieLeg, ...]
    total_flight_energy_kwh: float
    return_soc: float
    safety_margin_soc: float
    total_flight_time_s: float
    total_handover_s: float
    total_operation_s: float
    mass_feasible: bool
    volume_feasible: bool
    energy_feasible: bool

    @property
    def feasible(self) -> bool:
        return self.mass_feasible and self.volume_feasible and self.energy_feasible


def evaluate_transport_sortie(
    scenario: Scenario,
    segments: SegmentMatrix,
    model_id: str,
    route: Sequence[str],
    deliveries: Mapping[str, Sequence[str]],
    *,
    safety_margin_soc: float | None = None,
    energy_model: TransportEnergyModel = DEFAULT_ENERGY_MODEL,
) -> SortieEvaluation:
    """Compute each leg with the mass remaining after earlier service stops.

    All boxes must be delivered at one stop on the given route. Hard capacity
    and energy violations are reported in the result rather than hiding the
    calculations. Payload above structural capacity cannot enter L(q), so that
    case is rejected with SortieInputError and a validator can report it.
    """
    if model_id not in scenario.transport_models:
        raise SortieInputError(f"Unknown transport model {model_id}")
    model = scenario.transport_models[model_id]
    route = tuple(route)
    if len(route) < 3 or route[0] != "O01" or route[-1] != "O01":
        raise SortieInputError("A sortie must start and return to O01 with a service stop")
    if len(set(route[1:-1])) != len(route[1:-1]) or any(s not in scenario.services for s in route[1:-1]):
        raise SortieInputError("Service stops must be known and unique in a sortie")
    if set(deliveries) != set(route[1:-1]):
        raise SortieInputError("Deliveries must specify exactly the visited service stops")
    safety_margin = model.minimum_return_soc if safety_margin_soc is None else safety_margin_soc
    if not isfinite(safety_margin) or not 0 <= safety_margin <= 1:
        raise SortieInputError("Safety SOC must lie in [0,1]")
    all_ids = tuple(box_id for service in route[1:-1] for box_id in deliveries[service])
    if len(all_ids) != len(set(all_ids)) or not all_ids:
        raise SortieInputError("Each sortie box must appear exactly once and the sortie cannot be empty")
    boxes = []
    stops = []
    for service in route[1:-1]:
        ids = tuple(deliveries[service])
        if not ids:
            raise SortieInputError(f"Service {service} has no delivered boxes")
        service_boxes = []
        for box_id in ids:
            box = scenario.boxes.get(box_id)
            if box is None or box.service_id != service:
                raise SortieInputError(f"Unknown or wrongly assigned cargo box {box_id} at {service}")
            service_boxes.append(box)
        boxes.extend(service_boxes)
        stops.append(SortieStop(service, ids, fsum(b.mass_kg for b in service_boxes),
                                handover_time_s(model, len(ids))))
    loaded_mass = fsum(b.mass_kg for b in boxes)
    loaded_volume = fsum(b.volume_m3 for b in boxes)
    if loaded_mass > model.max_payload_kg + 1e-9:
        raise SortieInputError(f"Loaded mass {loaded_mass:g} kg exceeds {model_id} structural limit")
    remaining_mass = loaded_mass
    legs = []
    for start, end in zip(route, route[1:]):
        segment = segments.get(start, end)
        energy = transport_segment_energy(model, segment, remaining_mass,
                                          energy_model=energy_model)
        phases = flight_phases(model, segment)
        legs.append(SortieLeg(start, end, remaining_mass, segment.horizontal_distance_m,
                              energy.equivalent_range_m, energy.horizontal_kwh,
                              energy.climb_additional_kwh, energy.total_kwh,
                              phases.climb_s, phases.cruise_s, phases.descent_s))
        if end != "O01":
            remaining_mass -= stops[len(legs) - 1].delivered_mass_kg
            if abs(remaining_mass) < 1e-9:
                remaining_mass = 0.0
    if abs(remaining_mass) > 1e-8:
        raise SortieInputError("Route returns with unaccounted payload")
    total_energy = fsum(leg.total_energy_kwh for leg in legs)
    return_soc = 1.0 - total_energy / model.usable_energy_kwh
    total_flight_time = fsum(leg.flight_time_s for leg in legs)
    total_handover = fsum(stop.handover_s for stop in stops)
    prep = preparation_time_s(model, len(all_ids))
    return SortieEvaluation(route, model_id, all_ids, loaded_mass, loaded_volume,
                            prep, tuple(stops), tuple(legs), total_energy,
                            return_soc, safety_margin, total_flight_time,
                            total_handover, prep + total_flight_time + total_handover,
                            loaded_mass <= model.max_payload_kg + 1e-9,
                            loaded_volume <= model.max_volume_m3 + 1e-9,
                            total_energy <= (1.0 - safety_margin) * model.usable_energy_kwh + 1e-9)
