"""Typed input records. Suffixes state the physical units used internally."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Node:
    node_id: str
    name: str
    longitude_deg: float
    latitude_deg: float
    ground_altitude_m: float  # Official node sheet, never overwritten from DEM.
    population: int | None = None

    @property
    def operating_altitude_m(self) -> float:
        return self.ground_altitude_m + (0.0 if self.node_id == "O01" else 30.0)


@dataclass(frozen=True)
class CargoBox:
    box_id: str
    service_id: str
    material_type: str
    mass_kg: float
    volume_m3: float
    first_batch: bool
    first_batch_deadline_s: float | None
    desired_delivery_s: float
    emergency_priority: float  # Unitless.


@dataclass(frozen=True)
class Demand:
    service_id: str
    material_type: str
    box_count: int
    first_batch_count: int
    mass_per_box_kg: float
    volume_per_box_m3: float
    emergency_priority: float
    first_batch_deadline_s: float | None
    desired_delivery_s: float


@dataclass(frozen=True)
class TransportModel:
    model_id: str
    name: str
    empty_mass_with_battery_kg: float
    max_payload_kg: float
    max_volume_m3: float
    cruise_speed_m_s: float
    empty_range_m: float
    full_range_m: float
    usable_energy_kwh: float
    minimum_return_soc: float  # 0–1, Excel percent divided by 100.
    fixed_preparation_s: float
    loading_per_box_s: float
    base_handover_s: float
    handover_per_box_s: float
    climb_speed_m_s: float
    descent_speed_m_s: float
    climb_efficiency: float
    descent_efficiency: float


@dataclass(frozen=True)
class TransportDrone:
    drone_id: str
    model_id: str
    initial_node_id: str


@dataclass(frozen=True)
class BatteryStock:
    model_id: str
    count: int  # Includes batteries initially mounted on drones.
    full_charge_s: float
    initial_soc: float = 1.0


@dataclass(frozen=True)
class RelayModel:
    model_id: str
    name: str
    empty_mass_with_component_kg: float
    communication_module_mass_kg: float
    planned_takeoff_mass_kg: float
    cruise_speed_m_s: float
    cruise_power_kw: float
    usable_energy_kwh: float
    minimum_return_soc: float
    fixed_preparation_s: float
    link_setup_s: float
    turnaround_s: float
    climb_speed_m_s: float
    descent_speed_m_s: float
    climb_efficiency: float
    descent_efficiency: float
    hover_power_kw: float
    communication_power_kw: float
    max_hover_agl_m: float


@dataclass(frozen=True)
class RelayDrone:
    drone_id: str
    model_id: str
    initial_node_id: str


@dataclass(frozen=True)
class EnergyComponentStock:
    model_id: str
    count: int
    full_charge_s: float
    initial_soc: float = 1.0


@dataclass(frozen=True)
class RadioInterface:
    transmit_power_dbm: float
    antenna_gain_dbi: float  # Same gain used for Tx/Rx per attachment.


@dataclass(frozen=True)
class CommunicationParameters:
    frequency_mhz: float
    system_loss_db: float
    obstruction_loss_db: float
    receiver_sensitivity_dbm: float
    fade_margin_db: float
    transport: RadioInterface
    relay_access: RadioInterface
    relay_backhaul: RadioInterface
    gateway: RadioInterface
    gateway_antenna_agl_m: float


@dataclass(frozen=True)
class Scenario:
    root: Path
    dispatch: Node
    services: dict[str, Node]
    demands: tuple[Demand, ...]
    boxes: dict[str, CargoBox]
    transport_models: dict[str, TransportModel]
    transport_drones: dict[str, TransportDrone]
    battery_stocks: dict[str, BatteryStock]
    relay_models: dict[str, RelayModel]
    relay_drones: dict[str, RelayDrone]
    component_stocks: dict[str, EnergyComponentStock]
    communication: CommunicationParameters

    @property
    def nodes(self) -> dict[str, Node]:
        return {self.dispatch.node_id: self.dispatch, **self.services}
