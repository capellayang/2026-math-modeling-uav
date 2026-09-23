"""Explicit placeholders for absent official transport energy component rules."""

from dataclasses import dataclass

from relief_uav.data.models import TransportModel
from relief_uav.geo.segments import DirectedSegment


class MissingOfficialFormulaError(NotImplementedError):
    """Source DOCX/attachments do not define this energy component."""


@dataclass(frozen=True)
class TransportEnergyComponents:
    horizontal_kwh: float
    climb_additional_kwh: float

    @property
    def total_kwh(self) -> float:
        return self.horizontal_kwh + self.climb_additional_kwh


def horizontal_transport_energy_kwh(
    model: TransportModel, segment: DirectedSegment, payload_kg: float,
) -> float:
    raise MissingOfficialFormulaError(
        "E_hor formula is absent from the supplied original problem and attachments"
    )


def climb_additional_energy_kwh(
    model: TransportModel, segment: DirectedSegment, payload_kg: float,
) -> float:
    raise MissingOfficialFormulaError(
        "E_up formula is absent from the supplied original problem and attachments"
    )
