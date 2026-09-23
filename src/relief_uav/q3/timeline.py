"""Assemble a complete Q3 snapshot from transport and relay decisions."""

from relief_uav.communication.coverage import RadioEnvironment, coverage_intervals
from relief_uav.q2.model import Q2Solution
from .model import Q3Objective, Q3Solution, RelaySortie


def build_q3_solution(env: RadioEnvironment, transport: Q2Solution,
                      relays: tuple[RelaySortie, ...], *, seed: int,
                      search_seconds: float, cp_sat_status: str,
                      pareto_count: int = 1, alns_iterations: int = 0,
                      setup_energy_mode: str = "hover_plus_comm",
                      coverage_step_s: float | None = 1.0) -> Q3Solution:
    intervals = (() if coverage_step_s is None else
                 tuple(row for sortie in transport.sorties
                       for row in coverage_intervals(env, sortie, relays,
                                         max_step_s=coverage_step_s)[0]))
    t = transport.objective
    relay_energy = sum(r.total_energy_kwh for r in relays)
    objective = Q3Objective(t.weighted_tardiness, t.normalized_weighted_tardiness,
                            t.makespan_s,
                            max((t.makespan_s, *(r.return_o01_s for r in relays))),
                            t.total_energy_kwh, relay_energy,
                            t.total_energy_kwh+relay_energy,
                            len(transport.sorties), len(relays),
                            len(transport.sorties)+len(relays))
    return Q3Solution(transport, relays, intervals, objective, seed,
                      search_seconds, cp_sat_status, pareto_count,
                      alns_iterations, setup_energy_mode)
