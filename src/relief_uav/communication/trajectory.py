"""Exact piecewise-linear transport trajectory from Q2 stage timestamps."""

from dataclasses import dataclass

from relief_uav.data.models import Scenario
from relief_uav.q2.model import Q2Sortie
from .model import CommunicationEndpoint


@dataclass(frozen=True)
class TrajectoryPhase:
    name: str
    start_s: float
    end_s: float
    start: CommunicationEndpoint
    end: CommunicationEndpoint

    def at(self, t_s: float) -> CommunicationEndpoint:
        if not self.start_s - 1e-8 <= t_s <= self.end_s + 1e-8:
            raise ValueError("Time outside trajectory phase")
        ratio = 0.0 if self.end_s == self.start_s else max(0.0, min(1.0,
            (t_s-self.start_s)/(self.end_s-self.start_s)))
        return CommunicationEndpoint(
            self.start.longitude_deg + ratio*(self.end.longitude_deg-self.start.longitude_deg),
            self.start.latitude_deg + ratio*(self.end.latitude_deg-self.start.latitude_deg),
            self.start.altitude_m + ratio*(self.end.altitude_m-self.start.altitude_m))


def transport_phases(scenario: Scenario, sortie: Q2Sortie) -> tuple[TrajectoryPhase, ...]:
    result = []
    for index, leg in enumerate(sortie.legs):
        start_node, end_node = scenario.nodes[leg.start_id], scenario.nodes[leg.end_id]
        a = CommunicationEndpoint(start_node.longitude_deg, start_node.latitude_deg,
                                  start_node.operating_altitude_m)
        b = CommunicationEndpoint(end_node.longitude_deg, end_node.latitude_deg,
                                  end_node.operating_altitude_m)
        high_a = CommunicationEndpoint(a.longitude_deg, a.latitude_deg, leg.cruise_altitude_m)
        high_b = CommunicationEndpoint(b.longitude_deg, b.latitude_deg, leg.cruise_altitude_m)
        result.extend((
            TrajectoryPhase(f"leg{index+1}:climb", leg.climb_start_s, leg.climb_end_s, a, high_a),
            TrajectoryPhase(f"leg{index+1}:cruise", leg.cruise_start_s, leg.cruise_end_s, high_a, high_b),
            TrajectoryPhase(f"leg{index+1}:descent", leg.descent_start_s, leg.descent_end_s, high_b, b),
        ))
        if index < len(sortie.stops):
            stop = sortie.stops[index]
            result.append(TrajectoryPhase(f"{stop.service_id}:handover",
                                          stop.handover_start_s, stop.handover_end_s, b, b))
    return tuple(result)
