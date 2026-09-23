"""Communication-oriented Q2 route mutation before coupled CP refinement."""

from dataclasses import dataclass
from math import ceil
import random
from time import monotonic

from relief_uav.communication.coverage import RadioEnvironment, coverage_intervals
from relief_uav.geo.segments import SegmentMatrix
from relief_uav.q2.alns import AdaptiveWeights, fast_schedule_cached
from relief_uav.q2.cache import SortieEvaluationCache
from relief_uav.q2.model import Q2Solution, Q2SortieSpec
from relief_uav.q2.search import make_spec, mutate, number, hard_feasible
from relief_uav.q2.timeline import construct_sortie
from .model import Q3AlgorithmConfig


OPERATORS = ("worst_communication_removal", "low_link_margin_removal",
             "relay_conflict_reorder", "communication_hard_route_reorder",
             "q2_alns_mutation")


@dataclass(frozen=True)
class RouteCandidate:
    specs: tuple[Q2SortieSpec, ...]
    fast_transport: Q2Solution
    direct_blackout_s: float
    minimum_direct_margin_db: float
    score: float


@dataclass(frozen=True)
class RouteSearch:
    candidates: tuple[RouteCandidate, ...]
    history: tuple[dict, ...]
    iterations: int
    operator_weights: dict[str, float]
    elapsed_s: float


class RouteCommunicationCache:
    def __init__(self, env: RadioEnvironment, segments: SegmentMatrix,
                 step_s: float = 15.0):
        self.env, self.segments, self.step_s = env, segments, step_s
        self.records = {}

    def get(self, spec: Q2SortieSpec):
        key = (spec.model_id, spec.route,
               tuple((s.service_id, s.box_ids) for s in spec.deliveries))
        if key not in self.records:
            scenario = self.env.scenario
            drone = next(d.drone_id for d in scenario.transport_drones.values()
                         if d.model_id == spec.model_id)
            battery = f"{spec.model_id}-BAT-01"
            sortie = construct_sortie(scenario, self.segments, spec, drone, battery, 0)
            rows, _ = coverage_intervals(self.env, sortie, max_step_s=self.step_s)
            outage = sum(r.end_s-r.start_s for r in rows if r.mode == "OUTAGE")
            margin = min(r.minimum_margin_db for r in rows)
            self.records[key] = outage, margin
        return self.records[key]


def _targeted(specs: tuple[Q2SortieSpec, ...], risks: list[tuple[float, float]],
              operator: str, scenario, segments, rng):
    if operator == "q2_alns_mutation":
        return mutate(scenario, segments, specs, rng)[0]
    if operator == "low_link_margin_removal":
        index = min(range(len(specs)), key=lambda i: risks[i][1])
    else:
        index = max(range(len(specs)), key=lambda i: risks[i][0])
    chosen = specs[index]
    mapping = chosen.delivery_map()
    order = chosen.route[1:-1]
    result = list(specs)
    if operator in ("worst_communication_removal", "low_link_margin_removal"):
        # Move a whole service from the difficult route into a separate sortie;
        # cargo remains indivisible and common physics evaluates the new route.
        if len(order) > 1:
            service = rng.choice(order)
            boxes = mapping.pop(service)
            result[index] = make_spec(chosen.model_id, mapping,
                                      tuple(s for s in order if s != service))
            result.append(make_spec(chosen.model_id, {service: boxes}, (service,)))
        elif len(mapping[order[0]]) > 1:
            ids = mapping[order[0]]
            split = rng.randrange(1, len(ids))
            result[index] = make_spec(chosen.model_id, {order[0]: ids[:split]}, order)
            result.append(make_spec(chosen.model_id, {order[0]: ids[split:]}, order))
        else:
            return mutate(scenario, segments, specs, rng)[0]
    elif operator == "communication_hard_route_reorder" and len(order) > 1:
        result[index] = make_spec(chosen.model_id, mapping, tuple(reversed(order)))
    elif operator == "relay_conflict_reorder":
        if len(order) > 1:
            moved = list(order)
            moved.insert(rng.randrange(len(order)), moved.pop(rng.randrange(len(order))))
            result[index] = make_spec(chosen.model_id, mapping, tuple(moved))
        else:
            result.insert(0, result.pop(index))
    else:
        return mutate(scenario, segments, specs, rng)[0]
    return number(tuple(result))


def search_routes_q3(env: RadioEnvironment, segments: SegmentMatrix,
                     baseline: Q2Solution, config: Q3AlgorithmConfig,
                     *, budget_s: float = 120.0) -> RouteSearch:
    scenario = env.scenario
    started = monotonic()
    physics = SortieEvaluationCache(scenario, segments)
    radio = RouteCommunicationCache(env, segments)
    weights = AdaptiveWeights.create(OPERATORS, .20)
    original = tuple(s.spec for s in baseline.sorties)
    rng = random.Random(config.seed)
    history, archive, seen = [], [], set()
    actual = 0

    def candidate(specs):
        if any(not physics.feasible(spec) for spec in specs):
            return None
        fast = fast_schedule_cached(scenario, segments, specs, physics, rng)
        if not hard_feasible(scenario, fast):
            return None
        risk = [radio.get(spec) for spec in specs]
        blackout = sum(x[0] for x in risk)
        margin = min(x[1] for x in risk)
        j = fast.objective
        score = (20*j.weighted_tardiness/10000 + j.makespan_s/10000
                 + blackout/20000 + j.sortie_count/100)
        return RouteCandidate(specs, fast, blackout, margin, score)

    initial = candidate(original)
    if initial:
        archive.append(initial)
        seen.add(str(original))
    for restart in range(config.restarts):
        current = initial
        if current is None:
            break
        for iteration in range(ceil(config.iterations/config.restarts)):
            if actual >= config.iterations or monotonic()-started >= budget_s:
                break
            actual += 1
            operator = weights.choose(rng)
            risk = [radio.get(spec) for spec in current.specs]
            proposed = _targeted(current.specs, risk, operator, scenario, segments, rng)
            item = candidate(proposed) if proposed != current.specs else None
            reward = 0.0
            if item is not None:
                signature = str(item.specs)
                if signature not in seen:
                    archive.append(item)
                    seen.add(signature)
                    archive.sort(key=lambda x: x.score)
                    archive = archive[:25]
                if item.score < current.score:
                    current = item
                    reward = 6.0
                elif rng.random() < .03:
                    current = item
                    reward = 1.0
            weights.update(operator, reward)
            if actual == 1 or actual % 10 == 0:
                best = archive[0]
                history.append({"iteration": actual, "restart": restart,
                    "operator": operator, "score": best.score,
                    "j1": best.fast_transport.objective.weighted_tardiness,
                    "j2_s": best.fast_transport.objective.makespan_s,
                    "estimated_blackout_s": best.direct_blackout_s,
                    "routes": len(best.specs)})
        if monotonic()-started >= budget_s or actual >= config.iterations:
            break
    return RouteSearch(tuple(archive), tuple(history), actual,
                       dict(weights.weights), monotonic()-started)
