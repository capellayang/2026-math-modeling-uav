"""Memoized full-physics evaluations for repeated ALNS sortie specifications."""

from relief_uav.data.models import Scenario
from relief_uav.geo.segments import SegmentMatrix
from relief_uav.physics.sortie import SortieEvaluation, evaluate_transport_sortie
from .model import Q2SortieSpec


class SortieEvaluationCache:
    def __init__(self, scenario: Scenario, segments: SegmentMatrix):
        self.scenario = scenario
        self.segments = segments
        self._values: dict[tuple, SortieEvaluation] = {}
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(spec: Q2SortieSpec) -> tuple:
        return (spec.model_id, spec.route,
                tuple((stop.service_id, tuple(sorted(stop.box_ids)))
                      for stop in spec.deliveries))

    def get(self, spec: Q2SortieSpec) -> SortieEvaluation:
        key = self.key(spec)
        if key in self._values:
            self.hits += 1
            return self._values[key]
        result = evaluate_transport_sortie(
            self.scenario, self.segments, spec.model_id,
            spec.route, spec.delivery_map())
        self._values[key] = result
        self.misses += 1
        return result

    def feasible(self, spec: Q2SortieSpec) -> bool:
        try:
            return self.get(spec).feasible
        except (ValueError, KeyError):
            return False
