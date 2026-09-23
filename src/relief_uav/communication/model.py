"""Physical communication records. Coordinates are degrees, altitude is MSL metres."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CommunicationEndpoint:
    longitude_deg: float
    latitude_deg: float
    altitude_m: float


@dataclass(frozen=True)
class LinkEvaluation:
    distance_3d_m: float
    terrain_blocked: bool
    fspl_db: float
    obstruction_loss_db: float
    path_loss_db: float
    bidirectional_limit_db: float
    margin_db: float
    available: bool


@dataclass(frozen=True)
class CommunicationState:
    mode: str  # DIRECT, RELAY or OUTAGE.
    relay_sortie_id: str | None
    direct: LinkEvaluation
    relay_access: LinkEvaluation | None = None
    relay_backhaul: LinkEvaluation | None = None


@dataclass(frozen=True)
class CommunicationInterval:
    transport_sortie_id: str
    phase: str
    start_s: float
    end_s: float
    mode: str
    relay_sortie_id: str | None
    minimum_margin_db: float
    terrain_blocked: bool
    reason: str
