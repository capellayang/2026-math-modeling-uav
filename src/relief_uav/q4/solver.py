"""Exhaustive Q4 partition search; Q3 routes, times and links remain fixed."""

from dataclasses import asdict
from pathlib import Path

from relief_uav.data.models import Scenario
from relief_uav.q3.model import Q3Solution
from .inheritance import (atomic_components, frozen_q3_projection,
                          relay_bindings, source_sha256)
from .model import (POLICIES, Q4AlgorithmConfig, Q4Candidate, Q4PolicyResult,
                    Q4Solution, TaskGroup)
from .objectives import pareto_and_select, partition_metrics
from .partition import canonical_signature, enumerate_partitions
from .resources import global_minimum, group_metrics, stock_vector


def build_task_groups(q3: Q3Solution, groups: tuple[tuple[str, ...], ...],
                      k: int, policy: str) -> tuple[TaskGroup, ...]:
    if policy not in POLICIES:
        raise ValueError(f"Unknown relay policy: {policy}")
    by_service = {service: index for index, sites in enumerate(groups)
                  for service in sites}
    if len(by_service) != sum(map(len, groups)):
        raise ValueError("A service area appears in multiple groups")
    transports = [[] for _ in groups]
    by_transport = {}
    for sortie in q3.transport.sorties:
        sites = {n for n in sortie.spec.route if n != "O01"}
        labels = {by_service.get(n) for n in sites}
        if len(labels) != 1 or None in labels:
            raise ValueError(f"Multi-stop transport split or missing: {sortie.spec.sortie_id}")
        label = labels.pop()
        transports[label].append(sortie.spec.sortie_id)
        by_transport[sortie.spec.sortie_id] = label
    relay_tasks = [[] for _ in groups]
    for binding in relay_bindings(q3):
        rid = binding["relay_sortie_id"]
        labels = sorted({by_transport[tid] for tid in binding["transport_sorties"]})
        if policy == "strict_no_duplication" and len(labels) > 1:
            raise ValueError(f"Strict relay {rid} crosses task groups")
        relay = next(r for r in q3.relays if r.sortie_id == rid)
        for label in labels:
            gid = f"K{k}-G{label + 1}"
            task_id = rid if policy == "strict_no_duplication" else f"{gid}-{rid}"
            relay_tasks[label].append({"task_id": task_id,
                "source_sortie_id": rid, "group_id": gid,
                "snapshot": asdict(relay),
                "protected_transport_sorties": [tid for tid in binding["transport_sorties"]
                                                 if by_transport[tid] == label]})
    return tuple(TaskGroup(f"K{k}-G{i + 1}", tuple(sites),
                           tuple(sorted(transports[i])),
                           tuple(sorted(relay_tasks[i], key=lambda r: r["task_id"])))
                 for i, sites in enumerate(groups))


def solve_q4(scenario: Scenario, q3: Q3Solution, source_path: Path,
             q3_validation: dict, config: Q4AlgorithmConfig | None = None) -> Q4Solution:
    config = config or Q4AlgorithmConfig()
    if config.relay_policy not in ("strict", "replicate", "both"):
        raise ValueError("q4-relay-policy must be strict, replicate or both")
    if config.selection != "pareto_lexicographic":
        raise ValueError("Only exact Pareto + lexicographic selection is supported")
    if not q3_validation.get("passed") or q3_validation.get("outage_s", 1) > 1e-9:
        raise ValueError("Q4 requires Q3 validator PASS and zero communication outage")
    transport_atoms = atomic_components(scenario, q3, strict=False)
    strict_atoms = atomic_components(scenario, q3, strict=True)
    bindings = relay_bindings(q3)
    stock = stock_vector(scenario)
    global_min = global_minimum(scenario, q3)
    policy_results = []
    policies = POLICIES if config.relay_policy == "both" else (
        "strict_no_duplication",) if config.relay_policy == "strict" else POLICIES
    # The strict result is always included because it is the official submission.
    for policy in policies:
        atoms = strict_atoms if policy == "strict_no_duplication" else transport_atoms
        for k in (2, 3):
            if len(atoms) < k:
                policy_results.append(Q4PolicyResult(policy, k, len(atoms), False,
                    f"strict inheritance has {len(atoms)} indivisible components < K={k}"
                    if policy == "strict_no_duplication" else
                    f"transport inheritance has {len(atoms)} indivisible components < K={k}"))
                continue
            candidates = []
            for index, sites in enumerate(enumerate_partitions(atoms, k), 1):
                task_groups = build_task_groups(q3, sites, k, policy)
                metrics = tuple(group_metrics(scenario, q3, g) for g in task_groups)
                summary = partition_metrics(metrics, stock, global_min)
                candidates.append(Q4Candidate(
                    f"{policy}-K{k}-P{index:03d}", k, policy,
                    canonical_signature(sites), metrics, **summary,
                    original_relay_task_count=len(q3.relays),
                    effective_relay_task_count=sum(len(g.group.relay_tasks) for g in metrics)))
            marked, selected, pareto_ids = pareto_and_select(tuple(candidates))
            policy_results.append(Q4PolicyResult(policy, k, len(atoms), True,
                None, marked, selected, pareto_ids))
    return Q4Solution(source_sha256(source_path), frozen_q3_projection(q3),
        q3_validation, transport_atoms, strict_atoms, bindings,
        stock, global_min, tuple(policy_results), config)
