"""Gateway-first direct/relay/outage classification."""

from math import hypot

from pyproj import Geod

from relief_uav.data.models import Scenario
from relief_uav.geo.dem import DigitalElevationModel
from .link_budget import bidirectional_limit_db, free_space_loss_db
from .model import (CommunicationEndpoint, CommunicationInterval,
                    CommunicationState, LinkEvaluation)
from .terrain_los import terrain_blocked
from .trajectory import transport_phases

_GEOD = Geod(ellps="WGS84")


def gateway_endpoint(scenario: Scenario) -> CommunicationEndpoint:
    o = scenario.dispatch
    return CommunicationEndpoint(o.longitude_deg, o.latitude_deg,
                                 o.ground_altitude_m + scenario.communication.gateway_antenna_agl_m)


class RadioEnvironment:
    def __init__(self, scenario: Scenario, dem: DigitalElevationModel):
        self.scenario, self.dem = scenario, dem
        self.gateway = gateway_endpoint(scenario)
        self.params = scenario.communication

    def link(self, a: CommunicationEndpoint, b: CommunicationEndpoint,
             kind: str) -> LinkEvaluation:
        p = self.params
        interfaces = {
            "direct": (p.transport, p.gateway),
            "access": (p.transport, p.relay_access),
            "backhaul": (p.relay_backhaul, p.gateway),
        }
        left, right = interfaces[kind]
        limit = bidirectional_limit_db(left, right, p)
        _, _, horizontal = _GEOD.inv(a.longitude_deg, a.latitude_deg,
                                      b.longitude_deg, b.latitude_deg)
        distance = hypot(horizontal, a.altitude_m-b.altitude_m)
        blocked = terrain_blocked(self.dem, a, b)
        fspl = free_space_loss_db(p.frequency_mhz, distance)
        obstruction = p.obstruction_loss_db if blocked else 0.0
        loss = fspl + obstruction
        margin = limit-loss
        return LinkEvaluation(distance, blocked, fspl, obstruction, loss,
                              limit, margin, margin >= 0)

    def state(self, transport: CommunicationEndpoint,
              active_relays: tuple[tuple[str, CommunicationEndpoint], ...] = ()) -> CommunicationState:
        direct = self.link(transport, self.gateway, "direct")
        if direct.available:
            return CommunicationState("DIRECT", None, direct)
        best = None
        for sortie_id, hover in active_relays:
            access = self.link(transport, hover, "access")
            if not access.available:
                continue
            backhaul = self.link(hover, self.gateway, "backhaul")
            if backhaul.available:
                candidate = (min(access.margin_db, backhaul.margin_db), sortie_id, access, backhaul)
                if best is None or candidate[0] > best[0]:
                    best = candidate
        if best is not None:
            _, sortie_id, access, backhaul = best
            return CommunicationState("RELAY", sortie_id, direct, access, backhaul)
        return CommunicationState("OUTAGE", None, direct)


def coverage_intervals(env: RadioEnvironment, sortie, relays=(), *,
                       max_step_s: float = 1.0,
                       transition_tolerance_s: float = 0.05):
    """Numerical time verification across all exact stage boundaries.

    A point is evaluated at both ends and at the midpoint of each cell. A cell
    with any differing state is recursively bisected to the transition
    tolerance. This is numerical verification, not an analytic proof.
    """
    if max_step_s <= 0 or transition_tolerance_s <= 0:
        raise ValueError("Verification step and transition tolerance must be positive")
    intervals = []
    samples = []

    def active(t):
        return tuple((r.sortie_id, CommunicationEndpoint(
            r.hover.longitude_deg, r.hover.latitude_deg, r.hover.hover_msl_m))
            for r in relays if r.service_start_s <= t <= r.service_end_s)

    for phase in transport_phases(env.scenario, sortie):
        if phase.end_s <= phase.start_s:
            continue
        n = max(1, __import__("math").ceil((phase.end_s-phase.start_s)/max_step_s))
        times = [phase.start_s + (phase.end_s-phase.start_s)*i/n for i in range(n+1)]
        # Relay service boundaries can change state even without motion.
        times.extend(t for r in relays for t in (r.service_start_s, r.service_end_s)
                     if phase.start_s < t < phase.end_s)
        times = sorted(set(times))

        state_cache = {}
        def state(t):
            if t not in state_cache:
                state_cache[t] = env.state(phase.at(t), active(t))
            return state_cache[t]

        def label(current):
            return current.mode, current.relay_sortie_id

        def refine(lo, hi, left, right):
            mid = (lo+hi)/2
            current = state(mid)
            if hi-lo <= transition_tolerance_s or (
                    label(left) == label(current) == label(right)):
                return [(lo, hi, current)]
            return (refine(lo, mid, left, current)
                    + refine(mid, hi, current, right))

        cells = []
        for lo, hi in zip(times, times[1:]):
            left, right = state(lo), state(hi)
            samples.append((sortie.spec.sortie_id, phase.name, lo, left))
            cells.extend(refine(lo, hi, left, right))
        samples.append((sortie.spec.sortie_id, phase.name, times[-1], state(times[-1])))
        for lo, hi, current in cells:
            samples.append((sortie.spec.sortie_id, phase.name, (lo+hi)/2, current))
            if hi <= lo:
                continue
            margin = (current.direct.margin_db if current.mode != "RELAY"
                      else min(current.relay_access.margin_db,
                               current.relay_backhaul.margin_db))
            reason = ("terrain_and_distance" if current.direct.terrain_blocked
                      and current.direct.fspl_db > current.direct.bidirectional_limit_db
                      else "terrain" if current.direct.terrain_blocked else "distance")
            row = CommunicationInterval(sortie.spec.sortie_id, phase.name, lo, hi,
                                         current.mode, current.relay_sortie_id,
                                         margin, current.direct.terrain_blocked,
                                         reason if current.mode != "DIRECT" else "")
            if intervals and (intervals[-1].transport_sortie_id == row.transport_sortie_id
                              and intervals[-1].phase == row.phase
                              and intervals[-1].mode == row.mode
                              and intervals[-1].relay_sortie_id == row.relay_sortie_id
                              and abs(intervals[-1].end_s-row.start_s) < 1e-7):
                old = intervals[-1]
                intervals[-1] = CommunicationInterval(old.transport_sortie_id,
                    old.phase, old.start_s, row.end_s, old.mode, old.relay_sortie_id,
                    min(old.minimum_margin_db, row.minimum_margin_db),
                    old.terrain_blocked or row.terrain_blocked,
                    row.reason if row.mode == "OUTAGE" else old.reason)
            else:
                intervals.append(row)
    return tuple(intervals), tuple(samples)
