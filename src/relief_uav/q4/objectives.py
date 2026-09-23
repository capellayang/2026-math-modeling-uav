"""Project-defined Q4 comparison metrics (the source states no unique weights)."""

from dataclasses import replace
from math import sqrt

from .model import Q4Candidate, Q4GroupMetrics, RESOURCE_KEYS


def partition_metrics(groups: tuple[Q4GroupMetrics, ...],
                      stock: dict[str, int], global_minimum: dict[str, int]) -> dict:
    vector = {key: sum(g.minimum_recolored_requirement[key] for g in groups)
              for key in RESOURCE_KEYS}
    overhead = {key: vector[key] - global_minimum[key] for key in RESOURCE_KEYS}
    if any(value < 0 for value in overhead.values()):
        raise AssertionError("Partition requires fewer resources than global minimum")
    surplus = {key: max(0, stock[key] - vector[key]) for key in RESOURCE_KEYS}
    gap = {key: max(0, vector[key] - stock[key]) for key in RESOURCE_KEYS}
    workloads = [g.total_workload_s for g in groups]
    mean = sum(workloads) / len(workloads)
    cv = sqrt(sum((w - mean) ** 2 for w in workloads) / len(workloads)) / mean
    range_w = (max(workloads) - min(workloads)) / mean
    return {"resource_vector": vector, "resource_scale": sum(vector.values()),
            "partition_redundancy": overhead,
            "partition_redundancy_total": sum(overhead.values()),
            "stock_surplus": surplus, "stock_gap": gap,
            "stock_gap_total": sum(gap.values()),
            "workload_cv": cv, "workload_range": range_w}


def pareto_and_select(candidates: tuple[Q4Candidate, ...]
                      ) -> tuple[tuple[Q4Candidate, ...], str, tuple[str, ...]]:
    def vector(c):
        return (c.stock_gap_total, c.resource_scale, c.workload_cv)

    def dominates(a, b):
        va, vb = vector(a), vector(b)
        return all(x <= y + 1e-10 for x, y in zip(va, vb)) and any(
            x < y - 1e-10 for x, y in zip(va, vb))

    pareto_ids = tuple(c.candidate_id for c in candidates if not any(
        dominates(other, c) for other in candidates if other is not c))
    selected = min(candidates, key=lambda c: (*vector(c), c.canonical_group_signature))
    return tuple(replace(c, pareto=c.candidate_id in pareto_ids,
                         selected=c.candidate_id == selected.candidate_id)
                 for c in candidates), selected.candidate_id, pareto_ids
