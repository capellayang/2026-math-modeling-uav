"""Relay geometry, energy, timeline and resource safeguards."""

from dataclasses import replace

import pytest

from relief_uav.data import load_scenario
from relief_uav.communication.coverage import RadioEnvironment
from relief_uav.communication.model import CommunicationInterval
from relief_uav.geo.dem import DigitalElevationModel
from relief_uav.geo.segments import build_segment_matrix, dem_source_path
from relief_uav.q2.report import load_q2_solution
from relief_uav.q3.timeline import build_q3_solution
from relief_uav.q3.relay_physics import (
    construct_relay_sortie, hover_point, relay_energy, relay_travel,
)
from relief_uav.validation.q3 import _no_overlap, validate_q3


@pytest.fixture(scope="module")
def source():
    scenario = load_scenario()
    return scenario, DigitalElevationModel(dem_source_path(scenario.root))


def test_hover_agl_and_msl(source):
    s, dem = source
    n = s.services["S011"]
    with pytest.raises(ValueError):
        hover_point(dem, n.longitude_deg, n.latitude_deg, 301,
                    next(iter(s.relay_models.values())).max_hover_agl_m)
    point = hover_point(dem, n.longitude_deg, n.latitude_deg, 300, 300)
    assert point.hover_msl_m == point.ground_elevation_m+300


def test_relay_energy_timeline_and_low_soc(source):
    s, dem = source
    n = s.services["S011"]
    point = hover_point(dem, n.longitude_deg, n.latitude_deg, 300, 300)
    travel = relay_travel(s, dem, point)
    assert travel.flight_altitude_m >= max(travel.maximum_dem_m+50,
                                            point.hover_msl_m,
                                            s.dispatch.ground_altitude_m)
    conservative = relay_energy(s, travel, 1000, "hover_plus_comm")
    alternative = relay_energy(s, travel, 1000, "hover_only")
    assert conservative[1] > alternative[1]
    sortie = construct_relay_sortie(s, dem, "R01", "R01", "R-COMP-01",
                                    point, 1000, 2000)
    assert sortie.link_setup_end_s == pytest.approx(sortie.service_start_s)
    assert sortie.return_soc >= next(iter(s.relay_models.values())).minimum_return_soc
    assert sortie.charge_start_s == sortie.return_o01_s
    with pytest.raises(ValueError, match="before service"):
        construct_relay_sortie(s, dem, "R00", "R01", "R-COMP-01",
                               point, 100, 300)
    with pytest.raises(ValueError, match="SOC"):
        construct_relay_sortie(s, dem, "R02", "R01", "R-COMP-02",
                               point, 1000, 9000)


def test_relay_entity_and_component_overlap_rejected(source):
    s, dem = source
    n = s.services["S011"]
    point = hover_point(dem, n.longitude_deg, n.latitude_deg, 300, 300)
    first = construct_relay_sortie(s, dem, "R01", "R01", "R-COMP-01",
                                   point, 1000, 1500)
    second = replace(first, sortie_id="R02", preparation_start_s=first.return_o01_s,
                     return_o01_s=first.return_o01_s+500)
    issues = []
    _no_overlap([first, second], lambda x:x.preparation_start_s,
                lambda x:x.turnaround_end_s, "relay entity", issues)
    assert any("overlap" in issue for issue in issues)
    issues = []
    _no_overlap([first, second], lambda x:x.preparation_start_s,
                lambda x:x.charge_end_s, "component", issues)
    assert any("overlap" in issue for issue in issues)


def test_independent_relay_validator_rejects_bad_resources_and_timing(source, monkeypatch):
    s, dem = source
    env = RadioEnvironment(s, dem)
    segments = build_segment_matrix(s)
    transport = load_q2_solution(s.root/"outputs/q3/baseline_q2_v2_summary.json")
    node = s.services["S011"]
    point = hover_point(dem,node.longitude_deg,node.latitude_deg,300,300)
    relay = construct_relay_sortie(s,dem,"Q3-T01","R01","R-COMP-01",point,1000,1500)
    def fake_coverage(env,sortie,relays=(),**kwargs):
        return ((CommunicationInterval(sortie.spec.sortie_id,"synthetic",
                sortie.takeoff_time_s,sortie.return_o01_time_s,"DIRECT",None,
                1.0,False,""),),())
    monkeypatch.setattr("relief_uav.validation.q3.coverage_intervals",fake_coverage)
    def check(relays):
        plan=build_q3_solution(env,transport,tuple(relays),seed=1,search_seconds=0,
                               cp_sat_status="TEST",coverage_step_s=None)
        return validate_q3(env,segments,plan,max_step_s=1)
    assert check([]).passed
    assert check([relay]).passed
    assert any("hover" in x or "geometry" in x for x in check([
        replace(relay,hover=replace(point,hover_agl_m=301))]).issues)
    assert any("link_setup_end" in x for x in check([
        replace(relay,link_setup_end_s=relay.service_start_s+5)]).issues)
    assert any("return SOC" in x for x in check([
        replace(relay,return_soc=.1)]).issues)
    second=construct_relay_sortie(s,dem,"Q3-T02","R01","R-COMP-02",point,1100,1600)
    assert any("relay entity" in x for x in check([relay,second]).issues)
    second=replace(second,drone_id="R02",component_id="R-COMP-01")
    assert any("energy component" in x for x in check([relay,second]).issues)
    reused_start=max(relay.charge_end_s,relay.turnaround_end_s)+1200
    reused=construct_relay_sortie(s,dem,"Q3-T03","R01","R-COMP-01",point,
                                  reused_start,reused_start+500)
    assert check([relay,reused]).passed
    early=construct_relay_sortie(s,dem,"Q3-T04","R01","R-COMP-02",point,
                                 relay.return_o01_s+600,relay.return_o01_s+1100)
    assert any("relay entity" in x for x in check([relay,early]).issues)
