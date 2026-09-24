"""Regression checks for source-faithful Q3 experiment search space."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from relief_uav.communication.coverage import RadioEnvironment
from relief_uav.communication.model import CommunicationEndpoint
from relief_uav.data import load_scenario
from relief_uav.geo.dem import DigitalElevationModel
from relief_uav.geo.segments import dem_source_path
from relief_uav.q3 import candidates
from relief_uav.q3.model import Q3AlgorithmConfig
from relief_uav.q3.relay_physics import construct_relay_sortie, hover_point
from relief_uav.q3.search import RouteCandidate, _multiobjective_archive
from relief_uav.validation.q3 import _no_overlap


ROOT = Path(__file__).resolve().parents[1]


def test_full_altitude_mode_evaluates_every_configured_height(monkeypatch):
    scenario = load_scenario(ROOT)
    env = RadioEnvironment(scenario, DigitalElevationModel(dem_source_path(ROOT)))
    service = next(iter(scenario.services.values()))
    monkeypatch.setattr(candidates, "_xy_pool", lambda *args: ((service.longitude_deg,
                                                       service.latitude_deg),))
    monkeypatch.setattr(candidates, "blackout_representatives", lambda *args: (
        ("T", 0, CommunicationEndpoint(service.longitude_deg,
                                        service.latitude_deg, service.ground_altitude_m+30)),))
    calls = []
    original = candidates.hover_point

    def record(dem, longitude, latitude, agl, maximum):
        calls.append(agl)
        return original(dem, longitude, latitude, agl, maximum)

    monkeypatch.setattr(candidates, "hover_point", record)
    monkeypatch.setattr(env, "link", lambda *args: SimpleNamespace(available=True,
                                                                    margin_db=10.0))
    monkeypatch.setattr(candidates, "relay_travel", lambda *args: SimpleNamespace(
        outbound_energy_kwh=.1, return_energy_kwh=.1,
        outbound_s=100.0, return_s=100.0))
    fake_sortie = SimpleNamespace(spec=SimpleNamespace(route=("O01", "S001", "O01")),
                                  takeoff_time_s=0.0)
    transport = SimpleNamespace(sorties=(fake_sortie,))
    audit = {"intervals": [{"transport_sortie_id": "T", "start_s": 0, "end_s": 1}]}
    config = replace(Q3AlgorithmConfig(), hover_altitudes_m=(50, 100, 150),
                     hover_altitude_mode="full", hover_top_k=3)
    points, _, _ = candidates.generate_candidates(env, transport, audit, config)
    assert {50, 100, 150}.issubset(calls)
    assert points


def test_relay_component_can_be_reused_after_exact_full_charge():
    scenario = load_scenario(ROOT)
    dem = DigitalElevationModel(dem_source_path(ROOT))
    service = next(iter(scenario.services.values()))
    point = hover_point(dem, service.longitude_deg, service.latitude_deg, 300, 300)
    first = construct_relay_sortie(scenario, dem, "R1", "R01", "R-COMP-01",
                                   point, 3000, 3100)
    lead = first.service_start_s-first.preparation_start_s
    second_start = first.charge_end_s + lead + 1
    second = construct_relay_sortie(scenario, dem, "R2", "R02", "R-COMP-01",
                                    point, second_start, second_start+100)
    issues = []
    _no_overlap([first, second], lambda r: r.preparation_start_s,
                lambda r: r.charge_end_s, "component", issues)
    assert not issues
    assert second.preparation_start_s > first.charge_end_s


def test_multiobjective_archive_preserves_demand_patterns():
    def candidate(score, j1, j2, pattern):
        obj = SimpleNamespace(weighted_tardiness=j1, makespan_s=j2,
                              total_energy_kwh=10.0, sortie_count=3)
        transport = SimpleNamespace(objective=obj)
        return RouteCandidate((), transport, 100.0, 0.0, score, pattern)
    rows = [candidate(1, 0, 100, (1,)), candidate(2, 0, 101, (1,)),
            candidate(3, 10, 90, (2,)), candidate(4, 5, 95, (3,))]
    kept = _multiobjective_archive(rows, limit=3)
    assert {x.demand_pattern for x in kept} == {(1,), (2,), (3,)}
