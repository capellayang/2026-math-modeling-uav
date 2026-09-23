"""Appendix 3 radio and raster LOS behavior."""

from dataclasses import replace
from math import isclose

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from relief_uav.communication.coverage import RadioEnvironment, gateway_endpoint
from relief_uav.communication.link_budget import (
    bidirectional_limit_db, directional_limit_db, free_space_loss_db,
    receiver_threshold_dbm,
)
from relief_uav.communication.model import CommunicationEndpoint, LinkEvaluation
from relief_uav.communication.trajectory import TrajectoryPhase, transport_phases
from relief_uav.communication.terrain_los import terrain_blocked
from relief_uav.data import load_scenario
from relief_uav.geo.dem import DemCoverageError, DigitalElevationModel
from relief_uav.geo.segments import dem_source_path
from relief_uav.q2.report import load_q2_solution
from pathlib import Path


def _small_dem(tmp_path, elevations):
    path = tmp_path / "los.tif"
    with rasterio.open(path, "w", driver="GTiff", height=len(elevations),
                       width=len(elevations[0]), count=1, dtype="float32",
                       crs="EPSG:4326", transform=from_origin(0, 3, 1, 1),
                       nodata=-32767) as dst:
        dst.write(np.array(elevations, dtype="float32"), 1)
    return DigitalElevationModel(path)


def test_appendix_three_values():
    s = load_scenario()
    p = s.communication
    assert receiver_threshold_dbm(p) == -90
    assert bidirectional_limit_db(p.transport, p.gateway, p) == 122
    assert bidirectional_limit_db(p.transport, p.relay_access, p) == 116
    assert bidirectional_limit_db(p.relay_backhaul, p.gateway, p) == 126
    assert directional_limit_db(p.transport,p.gateway,p) == 122
    assert directional_limit_db(p.gateway,p.transport,p) == 129
    assert gateway_endpoint(s).altitude_m == pytest.approx(147.7)
    assert free_space_loss_db(1000, 1000) == pytest.approx(92.45)
    assert free_space_loss_db(1000, 0) == float("-inf")
    with pytest.raises(ValueError):
        free_space_loss_db(1000, -1)


def test_terrain_plate_interior_corner_and_nodata(tmp_path):
    dem = _small_dem(tmp_path, [[0, 0, 0], [0, 11, 0], [0, 0, 0]])
    a = CommunicationEndpoint(0.5, 1.5, 10)
    b = CommunicationEndpoint(2.5, 1.5, 10)
    assert terrain_blocked(dem, a, b)
    assert not terrain_blocked(dem, replace(a, altitude_m=20), replace(b, altitude_m=20))
    # The high pixel touches the diagonal at a corner and is conservatively included.
    c = CommunicationEndpoint(0.5, 0.5, 10)
    d = CommunicationEndpoint(2.5, 2.5, 10)
    assert terrain_blocked(dem, c, d)
    dem.elevations_m[1, 1] = -32767
    with pytest.raises(DemCoverageError):
        terrain_blocked(dem, a, b)


def test_obstruction_adds_loss_instead_of_automatic_outage(tmp_path):
    s = load_scenario()
    dem = _small_dem(tmp_path, [[0, 0, 0], [0, 11, 0], [0, 0, 0]])
    env = RadioEnvironment(s, dem)
    a = CommunicationEndpoint(.5, 1.5, 10)
    b = CommunicationEndpoint(2.5, 1.5, 10)
    # At this artificial geographic scale the link is far; alter frequency only
    # to isolate the obstruction rule and preserve all link-budget parameters.
    env.params = replace(s.communication, frequency_mhz=0.001)
    link = env.link(a, b, "direct")
    assert link.terrain_blocked
    assert link.available
    assert link.obstruction_loss_db == s.communication.obstruction_loss_db
    assert link.path_loss_db == pytest.approx(link.fspl_db + link.obstruction_loss_db)
    env.params = replace(s.communication, frequency_mhz=65)
    assert not env.link(a, b, "direct").available


def test_real_dem_direct_link_and_gateway():
    s = load_scenario()
    env = RadioEnvironment(s, DigitalElevationModel(dem_source_path(s.root)))
    at_o = CommunicationEndpoint(s.dispatch.longitude_deg, s.dispatch.latitude_deg,
                                 s.dispatch.ground_altitude_m)
    assert env.link(at_o, env.gateway, "direct").available


def test_gateway_first_relay_two_hops_and_sharing(monkeypatch):
    s = load_scenario()
    env = RadioEnvironment(s, DigitalElevationModel(dem_source_path(s.root)))
    true = LinkEvaluation(1000, False, 90, 0, 90, 116, 26, True)
    false = LinkEvaluation(1000, True, 120, 10, 130, 116, -14, False)
    transport = CommunicationEndpoint(109.25, 23.04, 400)
    hover = CommunicationEndpoint(109.24, 23.03, 500)
    relay = (("R01", hover),)
    monkeypatch.setattr(env, "link", lambda a, b, kind: true)
    assert env.state(transport, relay).mode == "DIRECT"
    monkeypatch.setattr(env, "link", lambda a, b, kind: false if kind == "direct" else true)
    assert env.state(transport, relay).mode == "RELAY"
    assert env.state(CommunicationEndpoint(109.26, 23.05, 450), relay).mode == "RELAY"
    monkeypatch.setattr(env, "link", lambda a, b, kind: false if kind != "access" else true)
    assert env.state(transport, relay).mode == "OUTAGE"


def test_transport_piecewise_position():
    s = load_scenario()
    q = load_q2_solution(s.root/"outputs/q3/baseline_q2_v2_summary.json")
    sortie = q.sorties[0]
    phases = transport_phases(s, sortie)
    climb, cruise, descent, handover = phases[:4]
    assert climb.start.longitude_deg == climb.end.longitude_deg
    assert climb.at((climb.start_s+climb.end_s)/2).altitude_m == pytest.approx(
        (climb.start.altitude_m+climb.end.altitude_m)/2)
    assert cruise.at((cruise.start_s+cruise.end_s)/2).longitude_deg == pytest.approx(
        (cruise.start.longitude_deg+cruise.end.longitude_deg)/2)
    assert descent.start.longitude_deg == descent.end.longitude_deg
    assert handover.start == handover.end


def test_short_outage_is_detected_and_transition_refined(monkeypatch):
    from relief_uav.communication import coverage as module
    s = load_scenario()
    q = load_q2_solution(s.root/"outputs/q3/baseline_q2_v2_summary.json")
    a = CommunicationEndpoint(0,0,100)
    b = CommunicationEndpoint(2,0,100)
    phase = TrajectoryPhase("synthetic:cruise",0,2,a,b)
    monkeypatch.setattr(module,"transport_phases",lambda scenario,sortie:(phase,))
    class FakeEnvironment:
        scenario=s
        def state(self,position,active):
            from relief_uav.communication.model import CommunicationState
            available=not .9 < position.longitude_deg < 1.1
            link=LinkEvaluation(1,False,0,0,0,1,1 if available else -1,available)
            return CommunicationState("DIRECT" if available else "OUTAGE",None,link)
    rows,_=module.coverage_intervals(FakeEnvironment(),q.sorties[0],
                                     max_step_s=.25,transition_tolerance_s=.05)
    outage=sum(r.end_s-r.start_s for r in rows if r.mode=="OUTAGE")
    assert .15 < outage < .25
