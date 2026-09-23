from dataclasses import replace

import pytest

from relief_uav.data import load_scenario
from relief_uav.geo.segments import DirectedSegment
from relief_uav.physics import (
    charge_to_full_s, equivalent_range_m, flight_phases, handover_time_s,
    preparation_time_s, remaining_soc,
)
from relief_uav.physics.energy import (
    MissingOfficialFormulaError, climb_additional_energy_kwh,
    horizontal_transport_energy_kwh,
)


def test_equivalent_range_endpoints_and_midpoint():
    model = load_scenario().transport_models["A"]
    assert equivalent_range_m(model, 0) == model.empty_range_m == 25000
    assert equivalent_range_m(model, model.max_payload_kg) == pytest.approx(model.full_range_m)
    assert equivalent_range_m(model, model.max_payload_kg / 2) == pytest.approx(
        25000 - (25000 - 20000) * (0.5 ** 1.5))
    with pytest.raises(ValueError):
        equivalent_range_m(model, -0.01)
    with pytest.raises(ValueError):
        equivalent_range_m(model, model.max_payload_kg + 0.01)


def test_flight_has_three_separate_phases():
    model = load_scenario().transport_models["A"]
    segment = DirectedSegment("O01", "S001", 1200, 180, 230, 127.7, 184, 102.3, 46, 50)
    p = flight_phases(model, segment)
    assert p.climb_s == pytest.approx(102.3 / 3)
    assert p.cruise_s == 100
    assert p.descent_s == pytest.approx(46 / 2.5)
    assert p.total_s == pytest.approx(p.climb_s + p.cruise_s + p.descent_s)


def test_ground_service_parameters_direct_from_attachment():
    models = load_scenario().transport_models
    assert preparation_time_s(models["A"], 2) == 360
    assert handover_time_s(models["A"], 2) == 210
    assert preparation_time_s(models["C"], 2) == 360
    assert handover_time_s(models["C"], 2) == 252
    with pytest.raises(ValueError):
        preparation_time_s(models["A"], -1)


@pytest.mark.parametrize("soc,ratio", [(0.0, 1.0), (0.5, 0.35 + 0.65 * 0.4 / 0.9),
                                        (0.9, 0.35), (1.0, 0.0)])
def test_two_stage_charge_boundaries(soc, ratio):
    assert charge_to_full_s(soc, 1800) == pytest.approx(1800 * ratio)


def test_charge_continuity_and_soc_bookkeeping():
    t = 2400
    assert charge_to_full_s(0.9 - 1e-10, t) == pytest.approx(charge_to_full_s(0.9, t), abs=1e-6)
    assert charge_to_full_s(0.9 + 1e-10, t) == pytest.approx(charge_to_full_s(0.9, t), abs=1e-6)
    assert remaining_soc(4.5, 3.6) == pytest.approx(0.2)
    assert remaining_soc(4.5, 0) == 1.0
    with pytest.raises(ValueError):
        charge_to_full_s(1.01, t)


def test_missing_energy_formula_is_explicit():
    model = load_scenario().transport_models["A"]
    segment = DirectedSegment("A", "B", 1, 1, 51, 0, 0, 51, 51, 1)
    with pytest.raises(MissingOfficialFormulaError):
        horizontal_transport_energy_kwh(model, segment, 1)
    with pytest.raises(MissingOfficialFormulaError):
        climb_additional_energy_kwh(model, segment, 1)
