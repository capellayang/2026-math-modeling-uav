"""Replaceable transport energy model.

OFFICIAL: total segment energy is horizontal plus climb-additional energy;
the equivalent range is L0-(L0-LF)*(q/Q)**1.5; no descent addition.

PROJECT MODELING ASSUMPTIONS (not supplied as formulas in the problem):
  horizontal = usable battery energy * horizontal distance / equivalent range
  climb = (empty mass with battery + carried payload) * g * ascent height
          / (climb efficiency * 3.6e6), in kWh.
"""

from dataclasses import dataclass
from math import isfinite
from typing import Protocol

from relief_uav.data.models import TransportModel
from relief_uav.geo.segments import DirectedSegment
from .flight import equivalent_range_m

STANDARD_GRAVITY_M_S2 = 9.80665
JOULES_PER_KWH = 3_600_000.0


@dataclass(frozen=True)
class TransportEnergyComponents:
    payload_kg: float
    equivalent_range_m: float
    horizontal_kwh: float
    climb_additional_kwh: float
    descent_additional_kwh: float = 0.0

    @property
    def total_kwh(self) -> float:
        return self.horizontal_kwh + self.climb_additional_kwh + self.descent_additional_kwh


class TransportEnergyModel(Protocol):
    """A strategy that can be replaced after an official formula is supplied."""

    def evaluate(self, model: TransportModel, segment: DirectedSegment,
                 payload_kg: float) -> TransportEnergyComponents: ...


@dataclass(frozen=True)
class RangeGravityEnergyModel:
    """User-authorized project assumption, not an official energy formula."""

    gravity_m_s2: float = STANDARD_GRAVITY_M_S2

    def evaluate(self, model: TransportModel, segment: DirectedSegment,
                 payload_kg: float) -> TransportEnergyComponents:
        equivalent_range = equivalent_range_m(model, payload_kg)
        if model.usable_energy_kwh <= 0 or equivalent_range <= 0 or model.climb_efficiency <= 0:
            raise ValueError("Energy, equivalent range and climb efficiency must be positive")
        if not isfinite(self.gravity_m_s2) or self.gravity_m_s2 <= 0:
            raise ValueError("Gravity must be positive")
        if segment.horizontal_distance_m < 0 or segment.climb_m < 0:
            raise ValueError("Distance and climb must be nonnegative")
        horizontal = model.usable_energy_kwh * segment.horizontal_distance_m / equivalent_range
        climb = ((model.empty_mass_with_battery_kg + payload_kg) * self.gravity_m_s2
                 * segment.climb_m / (model.climb_efficiency * JOULES_PER_KWH))
        return TransportEnergyComponents(payload_kg, equivalent_range, horizontal, climb)


DEFAULT_ENERGY_MODEL = RangeGravityEnergyModel()


def transport_segment_energy(
    model: TransportModel, segment: DirectedSegment, payload_kg: float,
    *, energy_model: TransportEnergyModel = DEFAULT_ENERGY_MODEL,
) -> TransportEnergyComponents:
    return energy_model.evaluate(model, segment, payload_kg)


def horizontal_transport_energy_kwh(
    model: TransportModel, segment: DirectedSegment, payload_kg: float,
    *, energy_model: TransportEnergyModel = DEFAULT_ENERGY_MODEL,
) -> float:
    return transport_segment_energy(model, segment, payload_kg,
                                    energy_model=energy_model).horizontal_kwh


def climb_additional_energy_kwh(
    model: TransportModel, segment: DirectedSegment, payload_kg: float,
    *, energy_model: TransportEnergyModel = DEFAULT_ENERGY_MODEL,
) -> float:
    return transport_segment_energy(model, segment, payload_kg,
                                    energy_model=energy_model).climb_additional_kwh
