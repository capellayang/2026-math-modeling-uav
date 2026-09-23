"""Coupled transport start/resource and relay mission CP-SAT refinement.

Route physics is reused from Q2. This model optimizes departure/resource times
and relay locations/windows together for a fixed set of route specifications.
Communication eligibility is a search approximation; validation independently
recomputes all links at a finer time resolution.
"""

from dataclasses import dataclass
from math import ceil, floor

from ortools.sat.python import cp_model

from relief_uav.communication.coverage import RadioEnvironment
from relief_uav.communication.model import CommunicationEndpoint
from relief_uav.communication.trajectory import transport_phases
from relief_uav.geo.segments import SegmentMatrix
from relief_uav.physics.battery import charge_to_full_s
from relief_uav.physics.sortie import evaluate_transport_sortie
from relief_uav.q2.model import Q2Solution, Q2SortieSpec
from relief_uav.q2.timeline import (MEDICAL_TYPE, battery_inventory,
                                     build_solution, delivery_offsets_s)
from .model import Q3AlgorithmConfig, RelayHoverPoint
from .relay_physics import construct_relay_sortie, relay_energy, relay_travel

SCALE = 1000


def _ceil(value):
    return ceil(value*SCALE-1e-8)


@dataclass(frozen=True)
class CommunicationAtom:
    sortie_index: int
    relative_start_s: float
    relative_end_s: float
    eligible_candidates: tuple[int, ...]


@dataclass(frozen=True)
class JointSchedule:
    transport: Q2Solution
    relays: tuple
    cp_sat_status: str
    atom_count: int


def build_atoms(env: RadioEnvironment, transport: Q2Solution, audit: dict,
                candidates: tuple[RelayHoverPoint, ...]) -> tuple[CommunicationAtom, ...]:
    by_id = {s.spec.sortie_id: (i, s) for i, s in enumerate(transport.sorties)}
    phases = {sid: {p.name: p for p in transport_phases(env.scenario, sortie)}
              for sid, (_, sortie) in by_id.items()}
    endpoints = [CommunicationEndpoint(p.longitude_deg, p.latitude_deg, p.hover_msl_m)
                 for p in candidates]
    result = []
    for row in audit["intervals"]:
        sid = row["transport_sortie_id"]
        index, sortie = by_id[sid]
        phase = phases[sid][row["phase"]]
        positions = [phase.at(row["start_s"]+fraction*(row["end_s"]-row["start_s"]))
                     for fraction in (0.05, 0.25, 0.5, 0.75, 0.95)]
        eligible = tuple(j for j, endpoint in enumerate(endpoints)
                         if all(env.link(pos, endpoint, "access").available
                                for pos in positions))
        if not eligible:
            raise ValueError(f"No hover candidate covers {sid}/{row['phase']}")
        start = row["start_s"]-sortie.preparation_start_s
        end = row["end_s"]-sortie.preparation_start_s
        if result and result[-1].sortie_index == index and (
                start-result[-1].relative_end_s < 2.1 and
                result[-1].eligible_candidates == eligible):
            old = result[-1]
            result[-1] = CommunicationAtom(index, old.relative_start_s, end, eligible)
        else:
            result.append(CommunicationAtom(index, start, end, eligible))
    return tuple(result)


def schedule_joint(env: RadioEnvironment, segments: SegmentMatrix,
                   transport_seed: Q2Solution, audit: dict,
                   candidates: tuple[RelayHoverPoint, ...],
                   config: Q3AlgorithmConfig, *, time_limit_s: float = 120.0,
                   max_relay_sorties: int = 6) -> JointSchedule | None:
    scenario = env.scenario
    if max_relay_sorties > next(iter(scenario.component_stocks.values())).count:
        raise ValueError("Component-unique scheduling exceeds official stock")
    specs = tuple(s.spec for s in transport_seed.sorties)
    atoms = build_atoms(env, transport_seed, audit, candidates)
    if not atoms:
        return JointSchedule(transport_seed, (), "NO_RELAY_REQUIRED", 0)
    relay_model = next(iter(scenario.relay_models.values()))
    stock = next(iter(scenario.component_stocks.values()))
    travels = [relay_travel(scenario, env.dem, point) for point in candidates]
    leads = [_ceil(relay_model.fixed_preparation_s+relay_model.link_setup_s+t.outbound_s)
             for t in travels]
    tails = [_ceil(t.return_s) for t in travels]
    setup_energy = [relay_energy(scenario, t, 0,
                                config.relay_setup_energy_mode)[1] for t in travels]
    max_service = [max(0, floor((relay_model.usable_energy_kwh*(1-relay_model.minimum_return_soc)
              -t.outbound_energy_kwh-t.return_energy_kwh-se)
              *3600/(relay_model.hover_power_kw+relay_model.communication_power_kw)*SCALE))
              for t, se in zip(travels, setup_energy)]
    if any(limit <= 0 for limit in max_service):
        raise ValueError("Candidate exceeds relay energy reserve before service")
    evaluations = [evaluate_transport_sortie(scenario, segments, spec.model_id,
                    spec.route, spec.delivery_map()) for spec in specs]
    if not all(x.feasible for x in evaluations):
        return None
    durations = [_ceil(x.total_operation_s) for x in evaluations]
    charges = [_ceil(charge_to_full_s(x.return_soc,
                    scenario.battery_stocks[spec.model_id].full_charge_s))
               for x, spec in zip(evaluations, specs)]
    horizon = sum(durations)+sum(charges)+_ceil(10000)
    model = cp_model.CpModel()
    starts, returns, drone_choices, battery_choices = {}, {}, {}, {}
    drone_intervals = {ident: [] for ident in scenario.transport_drones}
    battery_inventory_map = battery_inventory(scenario)
    battery_intervals = {ident: [] for ident in battery_inventory_map}
    for i, (spec, evaluation) in enumerate(zip(specs, evaluations)):
        start = model.NewIntVar(0, horizon-durations[i]-charges[i], f"Tstart_{i}")
        end = model.NewIntVar(0, horizon, f"Treturn_{i}")
        model.Add(end == start+durations[i])
        starts[i], returns[i] = start, end
        drone_choices[i], battery_choices[i] = {}, {}
        for drone_id, drone in scenario.transport_drones.items():
            if drone.model_id != spec.model_id:
                continue
            flag = model.NewBoolVar(f"Tdrone_{i}_{drone_id}")
            drone_choices[i][drone_id] = flag
            drone_intervals[drone_id].append(model.NewOptionalIntervalVar(
                start, durations[i], end, flag, f"TdroneInterval_{i}_{drone_id}"))
        model.AddExactlyOne(drone_choices[i].values())
        for battery_id, battery_model in battery_inventory_map.items():
            if battery_model != spec.model_id:
                continue
            flag = model.NewBoolVar(f"Tbattery_{i}_{battery_id}")
            battery_choices[i][battery_id] = flag
            charge_end = model.NewIntVar(0, horizon, f"TbatteryAvailable_{i}_{battery_id}")
            model.Add(charge_end == start+durations[i]+charges[i])
            battery_intervals[battery_id].append(model.NewOptionalIntervalVar(
                start, durations[i]+charges[i], charge_end, flag,
                f"TbatteryInterval_{i}_{battery_id}"))
        model.AddExactlyOne(battery_choices[i].values())
        for stop, offset in zip(spec.deliveries, delivery_offsets_s(scenario, segments, spec)):
            delivered = start+_ceil(offset)
            for box_id in stop.box_ids:
                box = scenario.boxes[box_id]
                if box.material_type == MEDICAL_TYPE or (
                    config.tardiness_slack == 0 and config.absolute_epsilon == 0):
                    model.Add(delivered <= floor(box.desired_delivery_s*SCALE))
                if box.first_batch:
                    model.Add(delivered <= floor(box.first_batch_deadline_s*SCALE))
        hint = transport_seed.sorties[i]
        model.AddHint(start, round(hint.preparation_start_s*SCALE))
        for ident, flag in drone_choices[i].items():
            model.AddHint(flag, int(ident == hint.drone_id))
        for ident, flag in battery_choices[i].items():
            model.AddHint(flag, int(ident == hint.battery_id))
    for intervals in (*drone_intervals.values(), *battery_intervals.values()):
        if len(intervals) > 1:
            model.AddNoOverlap(intervals)

    active, location, relay_prep, service_start, service_end, relay_return = {}, {}, {}, {}, {}, {}
    energy_micro_terms = []
    entity_flags = {ident: [] for ident in scenario.relay_drones}
    entity_intervals = {ident: [] for ident in scenario.relay_drones}
    for m in range(max_relay_sorties):
        active[m] = model.NewBoolVar(f"Ractive_{m}")
        location[m] = [model.NewBoolVar(f"Rpoint_{m}_{j}") for j in range(len(candidates))]
        model.Add(sum(location[m]) == active[m])
        relay_prep[m] = model.NewIntVar(0, horizon, f"Rprep_{m}")
        service_start[m] = model.NewIntVar(0, horizon, f"RserviceStart_{m}")
        service_end[m] = model.NewIntVar(0, horizon, f"RserviceEnd_{m}")
        relay_return[m] = model.NewIntVar(0, horizon, f"Rreturn_{m}")
        model.Add(service_start[m] == relay_prep[m]
                  +sum(location[m][j]*leads[j] for j in range(len(candidates))))
        model.Add(relay_return[m] == service_end[m]
                  +sum(location[m][j]*tails[j] for j in range(len(candidates))))
        model.Add(service_end[m] >= service_start[m])
        model.Add(service_end[m]-service_start[m] <=
                  sum(location[m][j]*max_service[j] for j in range(len(candidates))))
        duration_ms = model.NewIntVar(0, horizon, f"RserviceDuration_{m}")
        model.Add(duration_ms == service_end[m]-service_start[m])
        service_micro = model.NewIntVar(0, 10_000_000, f"RserviceMicroKwh_{m}")
        # Attachment hover+communication power is 1.10 kW in this scenario.
        # Convert kW * ms / 3.6 to micro-kWh using an integer rational;
        # coefficients come from the loaded model, not a hardcoded 1.10.
        power_numerator = round((relay_model.hover_power_kw+
                                 relay_model.communication_power_kw)*10_000)
        service_numerator = model.NewIntVar(0, horizon*power_numerator,
                                            f"RenergyNumerator_{m}")
        model.Add(service_numerator == duration_ms*power_numerator)
        model.AddDivisionEquality(service_micro, service_numerator, 36_000)
        base_micro = sum(location[m][j]*round((travels[j].outbound_energy_kwh+
                        travels[j].return_energy_kwh+setup_energy[j])*1_000_000)
                         for j in range(len(candidates)))
        energy_micro_terms.append(service_micro+base_micro)
        model.Add(relay_prep[m] == 0).OnlyEnforceIf(active[m].Not())
        model.Add(service_end[m] == 0).OnlyEnforceIf(active[m].Not())
        entity_flags[m] = {}
        for drone_id in scenario.relay_drones:
            flag = model.NewBoolVar(f"Rentity_{m}_{drone_id}")
            entity_flags[m][drone_id] = flag
            occupied_end = model.NewIntVar(0, horizon, f"RturnEnd_{m}_{drone_id}")
            occupied_size = model.NewIntVar(0, horizon, f"Roccupied_{m}_{drone_id}")
            model.Add(occupied_end == relay_return[m]+_ceil(relay_model.turnaround_s)).OnlyEnforceIf(flag)
            model.Add(occupied_size == occupied_end-relay_prep[m]).OnlyEnforceIf(flag)
            entity_intervals[drone_id].append(model.NewOptionalIntervalVar(
                relay_prep[m], occupied_size, occupied_end, flag,
                f"Rinterval_{m}_{drone_id}"))
        model.Add(sum(entity_flags[m].values()) == active[m])
        if m:
            model.Add(active[m-1] >= active[m])
            model.Add(service_start[m-1] <= service_start[m]).OnlyEnforceIf(active[m])
    for intervals in entity_intervals.values():
        model.AddNoOverlap(intervals)
    for index, atom in enumerate(atoms):
        assigned = []
        # The direct audit is discretized. A five-second overlap protects
        # segment transitions while the independent validator checks at 0.25 s.
        guard_ms = _ceil(max(5.0, 2*config.search_step_s))
        for m in range(max_relay_sorties):
            flag = model.NewBoolVar(f"A_{index}_R_{m}")
            assigned.append(flag)
            model.Add(flag <= sum(location[m][j] for j in atom.eligible_candidates))
            model.Add(service_start[m] <= starts[atom.sortie_index]
                      +_ceil(atom.relative_start_s)-guard_ms).OnlyEnforceIf(flag)
            model.Add(service_end[m] >= starts[atom.sortie_index]
                      +_ceil(atom.relative_end_s)+guard_ms).OnlyEnforceIf(flag)
        model.AddExactlyOne(assigned)
    joint_end = model.NewIntVar(0, horizon, "joint_makespan")
    model.AddMaxEquality(joint_end, [*returns.values(), *relay_return.values()])
    # J1=0 is prioritized when the Q2 seed already achieves it. The integer
    # lexicographic proxy makes 1 ms of J2 more costly than the full possible
    # relay-energy swing, then breaks energy ties by relay sortie count.
    if transport_seed.objective.weighted_tardiness == 0:
        for i, spec in enumerate(specs):
            for stop, offset in zip(spec.deliveries, delivery_offsets_s(scenario, segments, spec)):
                for box_id in stop.box_ids:
                    box = scenario.boxes[box_id]
                    model.Add(starts[i]+_ceil(offset) <= floor(box.desired_delivery_s*SCALE))
    model.Minimize(joint_end*1_000_000_000
                   +sum(energy_micro_terms)*10+sum(active.values()))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.1, time_limit_s)
    solver.parameters.num_search_workers = 8
    solver.parameters.random_seed = config.seed
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    assignments = tuple((spec, next(d for d, flag in drone_choices[i].items()
                                    if solver.Value(flag)),
                         next(b for b, flag in battery_choices[i].items()
                              if solver.Value(flag)), solver.Value(starts[i])/SCALE)
                        for i, spec in enumerate(specs))
    transport = build_solution(scenario, segments, assignments, seed=config.seed,
                               objective_mode="pareto_epsilon")
    relays = []
    for m in range(max_relay_sorties):
        if not solver.Value(active[m]):
            continue
        j = next(j for j, flag in enumerate(location[m]) if solver.Value(flag))
        drone_id = next(d for d, flag in entity_flags[m].items() if solver.Value(flag))
        service_lo = solver.Value(service_start[m])/SCALE
        service_hi = solver.Value(service_end[m])/SCALE
        relays.append(construct_relay_sortie(scenario, env.dem, f"Q3-R{m+1:02d}",
            drone_id, f"R-COMP-{m+1:02d}", candidates[j], service_lo, service_hi,
            setup_energy_mode=config.relay_setup_energy_mode))
    return JointSchedule(transport, tuple(relays), solver.StatusName(status), len(atoms))
