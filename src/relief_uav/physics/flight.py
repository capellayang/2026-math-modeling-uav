"""Transport range, phase durations and attachment-specified ground service times."""

from dataclasses import dataclass
from math import isfinite

from relief_uav.data.models import TransportModel
from relief_uav.geo.segments import DirectedSegment


@dataclass(frozen=True)
class FlightPhases:
    climb_s: float
    cruise_s: float
    descent_s: float

    @property
    def total_s(self) -> float:
        return self.climb_s + self.cruise_s + self.descent_s


def equivalent_range_m(model: TransportModel, payload_kg: float) -> float:
    """Official L(q)=L0-(L0-LF)*(q/Q)^(3/2)."""
    if not isfinite(payload_kg) or not 0 <= payload_kg <= model.max_payload_kg:
        raise ValueError("Payload is outside [0, max_payload_kg]")
    if model.max_payload_kg <= 0:
        raise ValueError("Maximum payload must be positive")
    return model.empty_range_m - (model.empty_range_m - model.full_range_m) * (
        payload_kg / model.max_payload_kg
    ) ** 1.5


def flight_phases(model: TransportModel, segment: DirectedSegment) -> FlightPhases:
    """Official climb + horizontal cruise + descent, all results in seconds."""
    if any(v <= 0 for v in (model.climb_speed_m_s, model.cruise_speed_m_s, model.descent_speed_m_s)):
        raise ValueError("Flight speeds must be positive")
    if any(v < 0 for v in (segment.climb_m, segment.horizontal_distance_m, segment.descent_m)):
        raise ValueError("Segment heights and distance must be nonnegative")
    return FlightPhases(segment.climb_m / model.climb_speed_m_s,
                        segment.horizontal_distance_m / model.cruise_speed_m_s,
                        segment.descent_m / model.descent_speed_m_s)


def preparation_time_s(model: TransportModel, box_count: int) -> float:
    """Fixed workbench preparation plus per-box loading; no scheduling assumed."""
    if not isinstance(box_count, int) or box_count < 0:
        raise ValueError("Box count must be a nonnegative integer")
    return model.fixed_preparation_s + box_count * model.loading_per_box_s


def handover_time_s(model: TransportModel, box_count: int) -> float:
    """Receiving-point base handover plus per-box handover time."""
    if not isinstance(box_count, int) or box_count < 0:
        raise ValueError("Box count must be a nonnegative integer")
    return model.base_handover_s + box_count * model.handover_per_box_s
