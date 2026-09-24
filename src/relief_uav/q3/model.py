"""Q3 records; seconds, metres MSL/AGL, kWh and SOC in [0,1]."""

from dataclasses import dataclass

from relief_uav.q2.model import Q2Solution
from relief_uav.communication.model import CommunicationInterval


@dataclass(frozen=True)
class RelayHoverPoint:
    longitude_deg: float
    latitude_deg: float
    ground_elevation_m: float
    hover_agl_m: float
    hover_msl_m: float


@dataclass(frozen=True)
class RelayTravel:
    horizontal_distance_m: float
    maximum_dem_m: float
    flight_altitude_m: float
    outbound_climb_s: float
    outbound_cruise_s: float
    outbound_descent_s: float
    return_climb_s: float
    return_cruise_s: float
    return_descent_s: float
    outbound_energy_kwh: float
    return_energy_kwh: float

    @property
    def outbound_s(self):
        return self.outbound_climb_s+self.outbound_cruise_s+self.outbound_descent_s

    @property
    def return_s(self):
        return self.return_climb_s+self.return_cruise_s+self.return_descent_s


@dataclass(frozen=True)
class RelaySortie:
    sortie_id: str
    drone_id: str
    component_id: str
    hover: RelayHoverPoint
    travel: RelayTravel
    preparation_start_s: float
    takeoff_s: float
    outbound_climb_end_s: float
    outbound_cruise_end_s: float
    arrival_hover_s: float
    link_setup_start_s: float
    link_setup_end_s: float
    service_start_s: float
    service_end_s: float
    return_climb_end_s: float
    return_cruise_end_s: float
    return_o01_s: float
    turnaround_end_s: float
    outbound_energy_kwh: float
    setup_energy_kwh: float
    service_energy_kwh: float
    return_energy_kwh: float
    total_energy_kwh: float
    return_soc: float
    charge_start_s: float
    charge_end_s: float


@dataclass(frozen=True)
class Q3Objective:
    weighted_tardiness: float
    normalized_weighted_tardiness: float
    transport_makespan_s: float
    joint_makespan_s: float
    transport_energy_kwh: float
    relay_energy_kwh: float
    joint_energy_kwh: float
    transport_sortie_count: int
    relay_sortie_count: int
    joint_sortie_count: int


@dataclass(frozen=True)
class Q3Solution:
    transport: Q2Solution
    relays: tuple[RelaySortie, ...]
    communication: tuple[CommunicationInterval, ...]
    objective: Q3Objective
    seed: int
    search_seconds: float
    cp_sat_status: str
    pareto_count: int
    alns_iterations: int
    setup_energy_mode: str


@dataclass(frozen=True)
class Q3AlgorithmConfig:
    seed: int = 20260923
    time_limit_s: float = 600.0
    iterations: int = 2000
    restarts: int = 6
    search_step_s: float = 1.0
    validation_step_s: float = 0.25
    transition_tolerance_s: float = 0.05
    hover_grid_m: float = 600.0
    hover_altitudes_m: tuple[float, ...] = (50, 100, 150, 200, 250, 300)
    hover_top_k: int = 20
    cp_candidates: int = 12
    tardiness_slack: float = 0.05
    absolute_epsilon: float = 0.0
    selection: str = "epsilon_makespan"
    relay_setup_energy_mode: str = "hover_plus_comm"
    max_relay_sorties: int = 6
    tardiness_mode: str = "zero"
    hover_xy_mode: str = "local"
    hover_altitude_mode: str = "legacy"
    route_archive_mode: str = "scalar"
    joint_objective: str = "makespan"
