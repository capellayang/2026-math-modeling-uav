"""Coefficient sensitivity only; does not claim the missing energy formulas are official."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
sys.path.insert(0, str(ROOT/"scripts"))

from q3_q4_improvement import sheet
from relief_uav.data import load_scenario
from relief_uav.physics.battery import charge_to_full_s
from relief_uav.q3.report import load_q3_solution


def overlap_count(records):
    count = 0
    by_resource = {}
    for ident, start, end in records:
        by_resource.setdefault(ident, []).append((start, end))
    for rows in by_resource.values():
        ordered = sorted(rows)
        count += sum(a[1] > b[0]+1e-7 for a, b in zip(ordered, ordered[1:]))
    return count


def main():
    scenario = load_scenario(ROOT)
    q3 = load_q3_solution(ROOT/"outputs/q3/q3_summary.json")
    relay_model = next(iter(scenario.relay_models.values()))
    component = next(iter(scenario.component_stocks.values()))
    rows = []
    for horizontal_factor in (.9, 1.0, 1.1):
      for climb_factor in (.9, 1.0, 1.1):
        transport_total = relay_total = 0.0
        min_transport_soc = min_relay_soc = 1.0
        reserve_violations = 0
        battery_intervals = []
        component_intervals = []
        for sortie in q3.transport.sorties:
            model = scenario.transport_models[sortie.spec.model_id]
            horizontal = sum(l.horizontal_energy_kwh for l in sortie.legs)
            climb = sum(l.climb_energy_kwh for l in sortie.legs)
            energy = horizontal_factor*horizontal+climb_factor*climb
            transport_total += energy
            soc = 1-energy/model.usable_energy_kwh
            min_transport_soc = min(min_transport_soc, soc)
            reserve_violations += soc < model.minimum_return_soc-1e-8
            full_s = scenario.battery_stocks[sortie.spec.model_id].full_charge_s
            if soc >= 0:
                battery_intervals.append((sortie.battery_id,
                    sortie.preparation_start_s,
                    sortie.return_o01_time_s+charge_to_full_s(soc, full_s)))
        for sortie in q3.relays:
            travel = sortie.travel
            cruise = relay_model.cruise_power_kw*(
                travel.outbound_cruise_s+travel.return_cruise_s)/3600
            ascent = travel.outbound_energy_kwh+travel.return_energy_kwh-cruise
            service = sortie.setup_energy_kwh+sortie.service_energy_kwh
            energy = cruise+climb_factor*ascent+service
            relay_total += energy
            soc = 1-energy/relay_model.usable_energy_kwh
            min_relay_soc = min(min_relay_soc, soc)
            reserve_violations += soc < relay_model.minimum_return_soc-1e-8
            if soc >= 0:
                component_intervals.append((sortie.component_id,
                    sortie.preparation_start_s,
                    sortie.return_o01_s+charge_to_full_s(soc,
                                                       component.full_charge_s)))
        rows.append((horizontal_factor, climb_factor, transport_total,
                     relay_total, transport_total+relay_total,
                     min_transport_soc, min_relay_soc, reserve_violations,
                     overlap_count(battery_intervals),
                     overlap_count(component_intervals)))
    sheet("energy_assumption_sensitivity.xlsx", [
        "transport_horizontal_coefficient", "climb_coefficient",
        "transport_energy_kwh", "relay_energy_kwh", "joint_energy_kwh",
        "min_transport_SOC", "min_relay_SOC", "return_reserve_violations",
        "battery_reuse_conflicts", "component_reuse_conflicts"], rows)
    for row in rows:
        print(row, flush=True)


if __name__ == "__main__":
    main()
