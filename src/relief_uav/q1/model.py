"""Q1 result records; no entity drone or battery scheduling in this question."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SafePayload:
    service_id: str
    model_id: str
    one_way_distance_m: float
    maximum_dem_m: float
    cruise_altitude_m: float
    structural_payload_kg: float
    safe_payload_kg: float | None
    sortie_energy_kwh: float | None
    return_soc: float | None
    energy_limited: bool | None


@dataclass(frozen=True)
class Q1Trip:
    trip_id: str
    service_id: str
    model_id: str
    box_ids: tuple[str, ...]
    mass_kg: float
    volume_m3: float
    outgoing_payload_kg: float
    outgoing_energy_kwh: float
    returning_energy_kwh: float
    total_energy_kwh: float
    preparation_s: float
    outgoing_flight_s: float
    handover_s: float
    returning_flight_s: float
    operation_s: float
    return_soc: float


@dataclass(frozen=True)
class Q1Solution:
    safety_margin_soc: float
    objective_mode: str
    safe_payloads: tuple[SafePayload, ...]
    trips: tuple[Q1Trip, ...]
    total_trips: int
    total_energy_kwh: float
    total_operation_s: float
