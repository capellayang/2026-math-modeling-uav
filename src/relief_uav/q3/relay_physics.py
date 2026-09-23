"""Relay flight geometry, component SOC and complete service timeline."""

from relief_uav.data.models import Node, Scenario
from relief_uav.geo.dem import DigitalElevationModel
from relief_uav.physics.battery import charge_to_full_s, remaining_soc
from relief_uav.physics.energy import JOULES_PER_KWH, STANDARD_GRAVITY_M_S2
from .model import RelayHoverPoint, RelaySortie, RelayTravel


def hover_point(dem: DigitalElevationModel, longitude_deg: float,
                latitude_deg: float, agl_m: float, max_agl_m: float) -> RelayHoverPoint:
    if not 0 < agl_m <= max_agl_m:
        raise ValueError("Relay hover AGL outside allowed range")
    row, col = dem.containing_cell(longitude_deg, latitude_deg)
    if not dem.valid_cell(row, col):
        raise ValueError("Relay hover on NoData DEM cell")
    ground = float(dem.elevations_m[row, col])
    return RelayHoverPoint(longitude_deg, latitude_deg, ground, agl_m, ground+agl_m)


def relay_travel(scenario: Scenario, dem: DigitalElevationModel,
                 point: RelayHoverPoint) -> RelayTravel:
    model = next(iter(scenario.relay_models.values()))
    pseudo = Node("P", "relay hover", point.longitude_deg, point.latitude_deg,
                  point.ground_elevation_m)
    geo = dem.analyze(scenario.dispatch, pseudo)
    z0, zp = scenario.dispatch.ground_altitude_m, point.hover_msl_m
    high = max(geo.maximum_ground_m+50, z0, zp)
    up_out, down_out = high-z0, high-zp
    up_ret, down_ret = high-zp, high-z0
    cruise_s = geo.distance_m/model.cruise_speed_m_s
    cruise_energy = model.cruise_power_kw*cruise_s/3600
    factor = (model.planned_takeoff_mass_kg*STANDARD_GRAVITY_M_S2
              / (model.climb_efficiency*JOULES_PER_KWH))
    return RelayTravel(geo.distance_m, geo.maximum_ground_m, high,
                       up_out/model.climb_speed_m_s, cruise_s,
                       down_out/model.descent_speed_m_s,
                       up_ret/model.climb_speed_m_s, cruise_s,
                       down_ret/model.descent_speed_m_s,
                       cruise_energy+factor*up_out,
                       cruise_energy+factor*up_ret)


def relay_energy(scenario: Scenario, travel: RelayTravel, service_s: float,
                 setup_energy_mode: str = "hover_plus_comm") -> tuple[float, float, float, float]:
    model = next(iter(scenario.relay_models.values()))
    if service_s < 0:
        raise ValueError("Negative relay service duration")
    if setup_energy_mode not in ("hover_plus_comm", "hover_only"):
        raise ValueError("Unknown relay setup energy mode")
    setup_power = model.hover_power_kw + (
        model.communication_power_kw if setup_energy_mode == "hover_plus_comm" else 0)
    setup = setup_power*model.link_setup_s/3600
    service = (model.hover_power_kw+model.communication_power_kw)*service_s/3600
    return travel.outbound_energy_kwh, setup, service, travel.return_energy_kwh


def construct_relay_sortie(scenario: Scenario, dem: DigitalElevationModel,
                           sortie_id: str, drone_id: str, component_id: str,
                           point: RelayHoverPoint, service_start_s: float,
                           service_end_s: float, *,
                           setup_energy_mode: str = "hover_plus_comm") -> RelaySortie:
    model = next(iter(scenario.relay_models.values()))
    stock = next(iter(scenario.component_stocks.values()))
    travel = relay_travel(scenario, dem, point)
    preparation = service_start_s-model.link_setup_s-travel.outbound_s-model.fixed_preparation_s
    takeoff = preparation+model.fixed_preparation_s
    climb_end = takeoff+travel.outbound_climb_s
    cruise_end = climb_end+travel.outbound_cruise_s
    arrival = cruise_end+travel.outbound_descent_s
    setup_end = arrival+model.link_setup_s
    if preparation < -1e-8 or abs(setup_end-service_start_s) > 1e-7:
        raise ValueError("Relay cannot finish preparation, travel and setup before service")
    energies = relay_energy(scenario, travel, service_end_s-service_start_s,
                            setup_energy_mode)
    energy = sum(energies)
    soc = remaining_soc(model.usable_energy_kwh, energy)
    if soc < model.minimum_return_soc-1e-9:
        raise ValueError("Relay sortie violates return SOC reserve")
    return_climb_end = service_end_s+travel.return_climb_s
    return_cruise_end = return_climb_end+travel.return_cruise_s
    returned = return_cruise_end+travel.return_descent_s
    charge_end = returned+charge_to_full_s(soc, stock.full_charge_s)
    return RelaySortie(sortie_id, drone_id, component_id, point, travel,
                       preparation, takeoff, climb_end, cruise_end, arrival,
                       arrival, setup_end, service_start_s, service_end_s,
                       return_climb_end, return_cruise_end, returned,
                       returned+model.turnaround_s, *energies, energy, soc,
                       returned, charge_end)
