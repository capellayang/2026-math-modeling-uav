"""Layered Q3 search: direct audit, hover candidates, route ALNS, coupled CP."""

from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic

from relief_uav.communication.coverage import RadioEnvironment
from relief_uav.data.models import Scenario
from relief_uav.geo.segments import SegmentMatrix
from relief_uav.q2.report import load_q2_solution
from relief_uav.q2.scheduler_v2 import schedule_epsilon
from relief_uav.q2.timeline import build_solution
from relief_uav.validation.q2 import validate_q2
from relief_uav.validation.q3 import Q3Validation, validate_q3
from .audit import audit_fingerprint, direct_audit, save_direct_audit
from .candidates import generate_candidates
from .joint_cp import schedule_joint
from .model import Q3AlgorithmConfig, Q3Solution
from .search import RouteSearch, search_routes_q3
from .timeline import build_q3_solution


@dataclass(frozen=True)
class Q3Run:
    selected: Q3Solution
    validation: Q3Validation
    baseline_audit: dict
    pareto: tuple[dict, ...]
    history: tuple[dict, ...]
    routes_examined: int
    hover_candidate_count: int
    elapsed_s: float
    route_weights: dict[str, float]


def _candidate_row(identifier: str, solution: Q3Solution,
                   check: Q3Validation) -> dict:
    o = solution.objective
    return {"candidate_id": identifier, "seed": solution.seed,
        "j1": o.weighted_tardiness,
        "j1_norm": o.normalized_weighted_tardiness,
        "j2_joint_s": o.joint_makespan_s,
        "transport_energy_kwh": o.transport_energy_kwh,
        "relay_energy_kwh": o.relay_energy_kwh,
        "joint_energy_kwh": o.joint_energy_kwh,
        "transport_sorties": o.transport_sortie_count,
        "relay_sorties": o.relay_sortie_count,
        "joint_sorties": o.joint_sortie_count,
        "relay_service_seconds": sum(r.service_end_s-r.service_start_s
                                      for r in solution.relays),
        "direct_s": check.direct_communication_s,
        "relay_s": check.relay_communication_s,
        "outage_s": check.outage_s,
        "minimum_link_margin_db": min(
            x for x in (check.minimum_relay_access_margin_db,
                        check.minimum_relay_backhaul_margin_db) if x is not None),
        "cp_sat_status": solution.cp_sat_status,
        "validator_status": "PASS" if check.passed else "FAIL"}


def _pareto(rows: list[tuple[str, Q3Solution, Q3Validation]]) -> list[tuple]:
    result = []
    for candidate in rows:
        o = candidate[1].objective
        def dominates(x):
            other = x[1].objective
            if (other.weighted_tardiness <= o.weighted_tardiness+1e-8 and
                    other.joint_makespan_s <= o.joint_makespan_s+1e-8):
                if (other.weighted_tardiness < o.weighted_tardiness-1e-8 or
                        other.joint_makespan_s < o.joint_makespan_s-1e-8):
                    return True
                if (abs(other.weighted_tardiness-o.weighted_tardiness) <= 1e-8 and
                        abs(other.joint_makespan_s-o.joint_makespan_s) <= 1e-8):
                    return (other.joint_energy_kwh, other.joint_sortie_count,
                            x[0]) < (o.joint_energy_kwh, o.joint_sortie_count,
                                      candidate[0])
            return False
        if any(dominates(x) for x in rows if x is not candidate):
            continue
        result.append(candidate)
    return sorted(result,key=lambda row:(row[1].objective.weighted_tardiness,
        row[1].objective.joint_makespan_s,row[1].objective.joint_energy_kwh,
        row[1].objective.joint_sortie_count))


def solve_q3(scenario: Scenario, segments: SegmentMatrix,
             env: RadioEnvironment, config: Q3AlgorithmConfig,
             *, baseline_path: Path) -> Q3Run:
    started = monotonic()
    model = next(iter(scenario.relay_models.values()))
    if (config.time_limit_s <= 0 or config.iterations < 0 or config.restarts <= 0 or
            config.search_step_s <= 0 or config.validation_step_s <= 0 or
            config.transition_tolerance_s <= 0 or config.hover_grid_m <= 0 or
            config.hover_top_k <= 0 or config.cp_candidates <= 0 or
            not config.hover_altitudes_m or
            any(not 0 < h <= model.max_hover_agl_m for h in config.hover_altitudes_m) or
            config.tardiness_slack < 0 or config.absolute_epsilon < 0 or
            config.selection != "epsilon_makespan" or
            config.relay_setup_energy_mode not in ("hover_plus_comm", "hover_only")):
        raise ValueError("Invalid Q3 algorithm configuration")
    baseline = load_q2_solution(baseline_path)
    check = validate_q2(scenario, segments, baseline)
    if not check.passed:
        raise RuntimeError(f"Q2-v2 seed fails transport validation: {check.issues[:5]}")
    audit_path = scenario.root/"outputs/q3/baseline_q2_direct_audit.json"
    if audit_path.is_file():
        import json
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    else:
        audit = None
    if audit is None or audit.get("source_fingerprint") != audit_fingerprint(env, baseline):
        audit = direct_audit(env, baseline, step_s=2.0,
                             source="committed Q2-v2 solution")
        save_direct_audit(scenario.root, audit)
    hover, _, _ = generate_candidates(env, baseline, audit, config,
                     scenario.root/"outputs/q3/cache/relay_candidates.json")
    if not hover:
        raise RuntimeError("No relay hover candidates with valid backhaul and energy")
    found = []
    base_cp = schedule_joint(env, segments, baseline, audit, hover, config,
                             time_limit_s=min(100, config.time_limit_s*.2))
    if base_cp:
        candidate = build_q3_solution(env, base_cp.transport, base_cp.relays,
            seed=config.seed, search_seconds=monotonic()-started,
            cp_sat_status=base_cp.cp_sat_status,
            setup_energy_mode=config.relay_setup_energy_mode,
            coverage_step_s=None)
        validation = validate_q3(env, segments, candidate, max_step_s=1.0,
                                 transition_tolerance_s=config.transition_tolerance_s)
        if validation.passed:
            found.append(("Q3-BASE", candidate, validation))
    route_search = search_routes_q3(env, segments, baseline, config,
                  budget_s=min(120, max(1, config.time_limit_s*.25)))
    routes_examined = 0
    base_signature = str(tuple(s.spec for s in baseline.sorties))
    for route in route_search.candidates[:config.cp_candidates]:
        if monotonic()-started > config.time_limit_s*.85:
            break
        if str(route.specs) == base_signature:
            continue
        routes_examined += 1
        cp = schedule_epsilon(scenario, segments, route.specs, mode="makespan",
            time_limit_s=min(20, config.time_limit_s*.035), seed=config.seed+routes_examined,
            best_tardiness_integer=0, relative_epsilon=config.tardiness_slack,
            absolute_epsilon=config.absolute_epsilon)
        if cp is None:
            continue
        transport = build_solution(scenario, segments, cp.assignments, seed=config.seed,
                                    objective_mode="pareto_epsilon")
        if not validate_q2(scenario, segments, transport).passed:
            continue
        comm = direct_audit(env, transport, step_s=max(2,config.search_step_s))
        try:
            refined = schedule_joint(env, segments, transport, comm, hover, config,
                     time_limit_s=min(65, max(1,config.time_limit_s*.12)))
        except ValueError:
            continue
        if refined is None:
            continue
        candidate = build_q3_solution(env, refined.transport, refined.relays,
            seed=config.seed, search_seconds=monotonic()-started,
            cp_sat_status=refined.cp_sat_status,
            setup_energy_mode=config.relay_setup_energy_mode,
            coverage_step_s=None)
        validation = validate_q3(env, segments, candidate, max_step_s=1.0,
                                 transition_tolerance_s=config.transition_tolerance_s)
        if validation.passed:
            found.append((f"Q3-ALT-{routes_examined:02d}",candidate,validation))
    if not found:
        raise RuntimeError("No Q3 plan passed coupled transport/relay communication validation")
    front = _pareto(found)
    best_j1 = min(x[1].objective.weighted_tardiness for x in front)
    bound = (1+config.tardiness_slack)*best_j1+config.absolute_epsilon
    eligible = [x for x in front if x[1].objective.weighted_tardiness <= bound+1e-8]
    selected = min(eligible,key=lambda row:(row[1].objective.joint_makespan_s,
        row[1].objective.joint_energy_kwh,row[1].objective.joint_sortie_count))
    # Final independent check uses its own DEM/environment, not optimizer cache.
    from relief_uav.geo.dem import DigitalElevationModel
    from relief_uav.geo.segments import dem_source_path
    independent = RadioEnvironment(scenario, DigitalElevationModel(dem_source_path(scenario.root)))
    fine = validate_q3(independent, segments, selected[1],
        max_step_s=config.validation_step_s,
        transition_tolerance_s=config.transition_tolerance_s)
    if not fine.passed:
        raise RuntimeError(f"Fine Q3 validator FAIL: {fine.issues[:10]}")
    final = replace(selected[1], communication=fine.intervals,
                    search_seconds=monotonic()-started,
                    pareto_count=len(front),
                    alns_iterations=route_search.iterations)
    metadata = tuple({**_candidate_row(id_,solution,check),
                      "selected": id_ == selected[0]}
                     for id_,solution,check in front)
    return Q3Run(final,fine,audit,metadata,route_search.history,
                 routes_examined,len(hover),monotonic()-started,
                 route_search.operator_weights)
