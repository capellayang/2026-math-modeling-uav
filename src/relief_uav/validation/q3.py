"""Independent Q3 reconstruction from official data, DEM and saved decisions."""

from dataclasses import dataclass
from math import isclose

from relief_uav.communication.coverage import RadioEnvironment, coverage_intervals
from relief_uav.communication.model import CommunicationEndpoint
from relief_uav.geo.segments import SegmentMatrix
from relief_uav.q3.model import Q3Solution
from relief_uav.q3.relay_physics import construct_relay_sortie, hover_point
from .q2 import validate_q2


@dataclass(frozen=True)
class Q3Validation:
    passed: bool
    issues: tuple[str, ...]
    total_required_communication_s: float
    direct_communication_s: float
    relay_communication_s: float
    outage_s: float
    direct_ratio: float
    relay_ratio: float
    outage_ratio: float
    minimum_direct_margin_db: float
    minimum_relay_access_margin_db: float | None
    minimum_relay_backhaul_margin_db: float | None
    direct_to_relay_switches: int
    relay_to_direct_switches: int
    verification_max_step_s: float
    transition_tolerance_s: float
    intervals: tuple


def _near(a: float, b: float, tolerance: float = 0.005) -> bool:
    return isclose(a, b, rel_tol=0, abs_tol=tolerance)


def _no_overlap(rows, start, end, label, issues):
    ordered = sorted(rows, key=start)
    for left, right in zip(ordered, ordered[1:]):
        if end(left) > start(right)+0.005:
            issues.append(f"{label} overlap: {left.sortie_id} / {right.sortie_id}")


def validate_q3(env: RadioEnvironment, segments: SegmentMatrix,
                solution: Q3Solution, *, max_step_s: float = 0.25,
                transition_tolerance_s: float = 0.05) -> Q3Validation:
    scenario = env.scenario
    issues = []
    q2 = validate_q2(scenario, segments, solution.transport)
    if not q2.passed:
        issues += [f"transport {x.scope}/{x.check}: {x.detail}" for x in q2.issues]
    model = next(iter(scenario.relay_models.values()))
    stock = next(iter(scenario.component_stocks.values()))
    ids = set()
    for relay in solution.relays:
        if relay.sortie_id in ids:
            issues.append(f"Duplicate relay sortie ID {relay.sortie_id}")
        ids.add(relay.sortie_id)
        if relay.drone_id not in scenario.relay_drones:
            issues.append(f"Unknown relay drone {relay.drone_id}")
        if relay.component_id not in {
            f"R-COMP-{i:02d}" for i in range(1, stock.count+1)
        }:
            issues.append(f"Unknown project-internal component {relay.component_id}")
        if relay.return_soc < model.minimum_return_soc-1e-9:
            issues.append(f"{relay.sortie_id} return SOC below reserve")
        try:
            point = hover_point(env.dem, relay.hover.longitude_deg,
                                relay.hover.latitude_deg, relay.hover.hover_agl_m,
                                model.max_hover_agl_m)
            if not _near(point.ground_elevation_m, relay.hover.ground_elevation_m,
                         1e-5) or not _near(point.hover_msl_m, relay.hover.hover_msl_m, 1e-5):
                issues.append(f"{relay.sortie_id} hover MSL/ground differs from DEM")
            expected = construct_relay_sortie(scenario, env.dem, relay.sortie_id,
                        relay.drone_id, relay.component_id, point,
                        relay.service_start_s, relay.service_end_s,
                        setup_energy_mode=solution.setup_energy_mode)
            for field in ("preparation_start_s", "takeoff_s", "outbound_climb_end_s",
                          "outbound_cruise_end_s", "arrival_hover_s", "link_setup_start_s",
                          "link_setup_end_s", "return_climb_end_s",
                          "return_cruise_end_s", "return_o01_s", "turnaround_end_s",
                          "outbound_energy_kwh", "setup_energy_kwh",
                          "service_energy_kwh", "return_energy_kwh",
                          "total_energy_kwh", "return_soc", "charge_start_s",
                          "charge_end_s"):
                if not _near(getattr(relay, field), getattr(expected, field)):
                    issues.append(f"{relay.sortie_id} {field} differs from source physics")
            if relay.preparation_start_s < -0.005:
                issues.append(f"{relay.sortie_id} preparation before mission start")
        except (ValueError, IndexError) as exc:
            issues.append(f"{relay.sortie_id} geometry, timing or energy: {exc}")
        endpoint = CommunicationEndpoint(relay.hover.longitude_deg,
                                         relay.hover.latitude_deg,
                                         relay.hover.hover_msl_m)
        try:
            if not env.link(endpoint, env.gateway, "backhaul").available:
                issues.append(f"{relay.sortie_id} relay backhaul unavailable")
        except ValueError as exc:
            issues.append(f"{relay.sortie_id} backhaul LOS: {exc}")
    for drone_id in scenario.relay_drones:
        _no_overlap([r for r in solution.relays if r.drone_id == drone_id],
                    lambda r:r.preparation_start_s, lambda r:r.turnaround_end_s,
                    f"relay entity {drone_id}", issues)
    for component_id in {r.component_id for r in solution.relays}:
        _no_overlap([r for r in solution.relays if r.component_id == component_id],
                    lambda r:r.preparation_start_s, lambda r:r.charge_end_s,
                    f"energy component {component_id}", issues)
    all_intervals, direct_margins, access_margins, backhaul_margins = [], [], [], []
    switches = {("DIRECT", "RELAY"): 0, ("RELAY", "DIRECT"): 0}
    for sortie in solution.transport.sorties:
        rows, samples = coverage_intervals(env, sortie, solution.relays,
            max_step_s=max_step_s, transition_tolerance_s=transition_tolerance_s)
        all_intervals.extend(rows)
        for _, _, _, state in samples:
            direct_margins.append(state.direct.margin_db)
            if state.mode == "RELAY":
                access_margins.append(state.relay_access.margin_db)
                backhaul_margins.append(state.relay_backhaul.margin_db)
            elif state.mode == "OUTAGE":
                issues.append(f"{sortie.spec.sortie_id} sampled communication OUTAGE")
                break
        for left, right in zip(rows, rows[1:]):
            if (left.transport_sortie_id == right.transport_sortie_id and
                    (left.mode, right.mode) in switches):
                switches[(left.mode, right.mode)] += 1
    total = sum(s.return_o01_time_s-s.takeoff_time_s for s in solution.transport.sorties)
    direct = sum(r.end_s-r.start_s for r in all_intervals if r.mode == "DIRECT")
    relayed = sum(r.end_s-r.start_s for r in all_intervals if r.mode == "RELAY")
    outage = sum(r.end_s-r.start_s for r in all_intervals if r.mode == "OUTAGE")
    if outage > 1e-8:
        issues.append(f"Detected outage duration {outage:.6f} s")
    if not _near(total, direct+relayed+outage, 0.005):
        issues.append("Communication duration does not reconcile to takeoff-return intervals")
    obj = solution.objective
    expected_energy = solution.transport.objective.total_energy_kwh + sum(
        r.total_energy_kwh for r in solution.relays)
    expected_makespan = max(
        (solution.transport.objective.makespan_s,
         *(r.return_o01_s for r in solution.relays))
    )
    for field, actual, expected in (
        ("J1", obj.weighted_tardiness, solution.transport.objective.weighted_tardiness),
        ("J1 norm", obj.normalized_weighted_tardiness,
         solution.transport.objective.normalized_weighted_tardiness),
        ("transport makespan", obj.transport_makespan_s,
         solution.transport.objective.makespan_s),
        ("joint makespan", obj.joint_makespan_s, expected_makespan),
        ("transport energy", obj.transport_energy_kwh,
         solution.transport.objective.total_energy_kwh),
        ("relay energy", obj.relay_energy_kwh,
         sum(r.total_energy_kwh for r in solution.relays)),
        ("joint energy", obj.joint_energy_kwh, expected_energy)):
        if not _near(actual, expected):
            issues.append(f"Objective {field} mismatch")
    if (obj.transport_sortie_count != len(solution.transport.sorties) or
            obj.relay_sortie_count != len(solution.relays) or
            obj.joint_sortie_count != len(solution.transport.sorties)+len(solution.relays)):
        issues.append("Objective sortie counts mismatch")
    return Q3Validation(not issues, tuple(issues), total, direct, relayed, outage,
                        direct/total if total else 0.0,
                        relayed/total if total else 0.0,
                        outage/total if total else 0.0,
                        min(direct_margins, default=float("nan")),
                        min(access_margins) if access_margins else None,
                        min(backhaul_margins) if backhaul_margins else None,
                        switches[("DIRECT", "RELAY")], switches[("RELAY", "DIRECT")],
                        max_step_s, transition_tolerance_s, tuple(all_intervals))
