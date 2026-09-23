"""Typed Q2 records. All times are seconds from mission start and SOC is 0–1."""

from dataclasses import dataclass


@dataclass(frozen=True)
class StopAssignment:
    service_id: str
    box_ids: tuple[str, ...]


@dataclass(frozen=True)
class Q2SortieSpec:
    sortie_id: str
    model_id: str
    route: tuple[str, ...]
    deliveries: tuple[StopAssignment, ...]

    def delivery_map(self) -> dict[str, tuple[str, ...]]:
        return {stop.service_id: stop.box_ids for stop in self.deliveries}


@dataclass(frozen=True)
class Q2LegTimeline:
    start_id: str
    end_id: str
    payload_kg: float
    distance_m: float
    maximum_dem_m: float
    cruise_altitude_m: float
    climb_m: float
    descent_m: float
    equivalent_range_m: float
    horizontal_energy_kwh: float
    climb_energy_kwh: float
    climb_start_s: float
    climb_end_s: float
    cruise_start_s: float
    cruise_end_s: float
    descent_start_s: float
    descent_end_s: float


@dataclass(frozen=True)
class Q2StopTimeline:
    service_id: str
    box_ids: tuple[str, ...]
    arrival_time_s: float
    handover_start_s: float
    handover_end_s: float
    delivery_complete_time_s: float


@dataclass(frozen=True)
class Q2BoxDelivery:
    box_id: str
    sortie_id: str
    service_id: str
    delivery_complete_time_s: float
    desired_delivery_s: float
    first_batch_deadline_s: float | None
    emergency_priority: float
    weighted_tardiness: float


@dataclass(frozen=True)
class Q2BatteryUse:
    battery_id: str
    model_id: str
    sortie_id: str
    use_start_s: float
    use_end_s: float
    start_soc: float
    return_soc: float


@dataclass(frozen=True)
class Q2ChargeEvent:
    battery_id: str
    model_id: str
    sortie_id: str
    charge_start_s: float
    charge_end_s: float
    return_soc: float


@dataclass(frozen=True)
class Q2Sortie:
    spec: Q2SortieSpec
    drone_id: str
    battery_id: str
    preparation_start_s: float
    takeoff_time_s: float
    legs: tuple[Q2LegTimeline, ...]
    stops: tuple[Q2StopTimeline, ...]
    return_o01_time_s: float
    loaded_mass_kg: float
    loaded_volume_m3: float
    energy_kwh: float
    return_soc: float


@dataclass(frozen=True)
class Q2Objective:
    weighted_tardiness: float
    normalized_weighted_tardiness: float
    makespan_s: float
    total_energy_kwh: float
    sortie_count: int

    @property
    def lex_key(self) -> tuple[float, float, float, int]:
        return (self.weighted_tardiness, self.makespan_s,
                self.total_energy_kwh, self.sortie_count)


@dataclass(frozen=True)
class Q2Solution:
    objective_mode: str
    seed: int
    sorties: tuple[Q2Sortie, ...]
    box_deliveries: tuple[Q2BoxDelivery, ...]
    battery_uses: tuple[Q2BatteryUse, ...]
    charge_events: tuple[Q2ChargeEvent, ...]
    objective: Q2Objective
    search_seconds: float
    pareto_count: int
    energy_model_status: str = "project modeling assumption; not official component formulas"
