"""Q2-v2 Pareto, epsilon, targeted ALNS and cache tests."""

from dataclasses import replace
from math import floor
from pathlib import Path
import random

import pytest

from relief_uav.data import load_scenario
from relief_uav.geo import build_segment_matrix
from relief_uav.q2.alns import (AdaptiveWeights, DESTROY_OPERATORS,
                                critical_makespan_removal,
                                worst_tardiness_removal)
from relief_uav.q2.cache import SortieEvaluationCache
from relief_uav.q2.model import Q2Objective
from relief_uav.q2.objectives import (dominates_primary, epsilon_feasible,
                                      pareto_filter, select_candidate,
                                      tardiness_limit)
from relief_uav.q2.report import load_q2_solution
from relief_uav.q2.scheduler import schedule_cp_sat_epsilon
from relief_uav.q2.scheduler_v2 import J1_UNIT_SCALE


@pytest.fixture(scope="module")
def context():
    root = Path(__file__).resolve().parents[1]
    scenario = load_scenario(root)
    segments = build_segment_matrix(scenario)
    baseline = load_q2_solution(root / "outputs/q2_v2/baseline_v1/q2_summary.json")
    return scenario, segments, baseline


def objective(j1, j2, j3=10, j4=3):
    return Q2Objective(j1, 0.0, j2, j3, j4)


def test_primary_pareto_keeps_tradeoff_and_removes_dominated():
    a = objective(10, 20)
    b = objective(12, 15)
    c = objective(13, 21)
    front = pareto_filter([a, b, c])
    assert front == [a, b]
    assert not dominates_primary(a, b)
    assert not dominates_primary(b, a)
    assert dominates_primary(a, c)
    assert select_candidate(front, method="epsilon_makespan", tardiness_slack=0.2) is b
    assert select_candidate(front, method="ideal_distance") in (a, b)


def test_epsilon_limit_positive_and_zero():
    assert tardiness_limit(100, 0.05) == pytest.approx(105)
    assert epsilon_feasible(objective(104, 10), 100, 0.05)
    assert not epsilon_feasible(objective(106, 10), 100, 0.05)
    assert tardiness_limit(0, 0.10, 4.0) == pytest.approx(4.0)
    assert epsilon_feasible(objective(3, 10), 0, 0.10, 4.0)
    assert not epsilon_feasible(objective(5, 10), 0, 0.10, 4.0)


def test_cp_sat_epsilon_constraint(context):
    scenario, segments, baseline = context
    specs = tuple(s.spec for s in baseline.sorties[:3])
    first = schedule_cp_sat_epsilon(scenario, segments, specs, mode="tardiness",
                                    time_limit_s=3, seed=20260923)
    assert first is not None
    second = schedule_cp_sat_epsilon(
        scenario, segments, specs, mode="makespan", time_limit_s=3,
        seed=20260923, best_tardiness_integer=first.weighted_tardiness_integer,
        relative_epsilon=0.05, hint_assignments=first.assignments)
    assert second is not None
    assert second.weighted_tardiness_integer <= floor(
        first.weighted_tardiness_integer * 1.05 + 1e-7)


def test_worst_tardiness_operator_targets_late_box(context):
    _, _, baseline = context
    deliveries = list(baseline.box_deliveries)
    target = deliveries[-1]
    deliveries[-1] = replace(target, weighted_tardiness=1e12)
    changed = replace(baseline, box_deliveries=tuple(deliveries))
    assert worst_tardiness_removal(changed, 1) == (target.box_id,)


def test_critical_makespan_operator_targets_last_drone(context):
    _, _, baseline = context
    last = max(baseline.sorties, key=lambda s: s.return_o01_time_s)
    target_boxes = {box_id for stop in last.spec.deliveries for box_id in stop.box_ids}
    assert critical_makespan_removal(baseline, 1)[0] in target_boxes


def test_adaptive_weights_are_seed_reproducible():
    def sample():
        rng = random.Random(20260923)
        weights = AdaptiveWeights.create(DESTROY_OPERATORS, 0.2)
        chosen = []
        for _ in range(30):
            name = weights.choose(rng)
            chosen.append(name)
            weights.update(name, 6.0 if name == DESTROY_OPERATORS[0] else 0.0)
        return chosen, weights.weights
    assert sample() == sample()


def test_sortie_evaluation_cache_hit(context):
    scenario, segments, baseline = context
    cache = SortieEvaluationCache(scenario, segments)
    spec = baseline.sorties[0].spec
    first = cache.get(spec)
    second = cache.get(spec)
    assert first == second
    assert (cache.misses, cache.hits) == (1, 1)
