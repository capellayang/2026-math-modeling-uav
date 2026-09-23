from collections import Counter
from pathlib import Path

from relief_uav.data import load_scenario


def test_official_scenario_contract():
    s = load_scenario()
    assert s.dispatch.node_id == "O01"
    assert len(s.services) == 15 and len(s.boxes) == 80
    assert sum(b.mass_kg for b in s.boxes.values()) == 758
    assert round(sum(b.volume_m3 for b in s.boxes.values()), 3) == 2.011
    assert sum(b.first_batch for b in s.boxes.values()) == 30
    assert Counter(d.model_id for d in s.transport_drones.values()) == {"A": 4, "B": 2, "C": 2}
    assert {k: v.count for k, v in s.battery_stocks.items()} == {"A": 6, "B": 4, "C": 4}
    assert len(s.relay_drones) == 2 and s.component_stocks["R"].count == 6
    assert s.transport_models["A"].minimum_return_soc == 0.2
    assert s.relay_models["R"].minimum_return_soc == 0.2
    assert s.dispatch.ground_altitude_m == 127.7
    assert s.dispatch.operating_altitude_m == 127.7
    assert s.services["S001"].ground_altitude_m == 154
    assert s.services["S001"].operating_altitude_m == 184


def test_resolve_root_independent_of_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    s = load_scenario()
    assert (s.root / "TASK.md").is_file()


def test_source_identifiers_and_deadline_semantics():
    s = load_scenario()
    assert len(set(s.boxes)) == 80
    assert all(b.service_id in s.services for b in s.boxes.values())
    assert all((b.first_batch_deadline_s is not None) == b.first_batch for b in s.boxes.values())
    assert s.communication.frequency_mhz == 2400
    assert s.communication.gateway_antenna_agl_m == 20
