"""Q4 exact partition, inherited tasks, interval resources and saved validation."""

from dataclasses import asdict, replace
import json
from pathlib import Path

import pytest

from relief_uav.data import load_scenario
from relief_uav.q3.report import load_q3_solution
from relief_uav.q4.model import ResourceInterval, TaskGroup
from relief_uav.q4.inheritance import atomic_components
from relief_uav.q4.partition import enumerate_partitions
from relief_uav.q4.resources import color_intervals, maximum_overlap, task_intervals
from relief_uav.q4.solver import build_task_groups, solve_q4
from relief_uav.validation.q4 import validate_q4


ROOT = Path(__file__).resolve().parents[1]
Q3_PATH = ROOT / "outputs/q3/q3_summary.json"


@pytest.fixture(scope="module")
def context():
    scenario = load_scenario(ROOT)
    q3 = load_q3_solution(Q3_PATH)
    validation = json.loads(Q3_PATH.read_text(encoding="utf-8"))["validation"]
    solution = solve_q4(scenario, q3, Q3_PATH, validation)
    payload = json.loads(json.dumps(asdict(solution), ensure_ascii=False))
    return scenario, q3, solution, payload


def test_exact_components_and_candidate_counts(context):
    scenario, q3, solution, _ = context
    transport = atomic_components(scenario, q3, strict=False)
    strict = atomic_components(scenario, q3, strict=True)
    assert len(transport) == 6
    assert len(strict) == 3
    assert [c.service_areas for c in transport] == [
        ("S001",),
        ("S002", "S003", "S004", "S005", "S007", "S009", "S015"),
        ("S006",), ("S008",), ("S010", "S012", "S013"),
        ("S011", "S014")]
    assert len(tuple(enumerate_partitions(transport, 2))) == 31
    assert len(tuple(enumerate_partitions(transport, 3))) == 90
    assert len(tuple(enumerate_partitions(strict, 2))) == 3
    assert len(tuple(enumerate_partitions(strict, 3))) == 1
    assert [(r.relay_policy, r.k, len(r.candidates)) for r in solution.policy_results] == [
        ("strict_no_duplication", 2, 3), ("strict_no_duplication", 3, 1),
        ("replicate_relay", 2, 31), ("replicate_relay", 3, 90)]


def test_all_services_once_and_no_empty_group(context):
    scenario, q3, solution, payload = context
    for result in solution.policy_results:
        for candidate in result.candidates:
            services = [site for group in candidate.groups
                        for site in group.group.service_areas]
            assert sorted(services) == sorted(scenario.services)
            assert all(group.group.service_areas for group in candidate.groups)
    bad = json.loads(json.dumps(payload))
    bad["policy_results"][0]["candidates"][0]["groups"][0]["group"]["service_areas"] = []
    assert not validate_q4(scenario, q3, Q3_PATH, bad, q3_passed=True).passed


def test_multi_stop_transport_cannot_be_split(context):
    scenario, q3, _, _ = context
    illegal = (tuple(s for s in sorted(scenario.services) if s != "S002"),
               ("S002",))
    with pytest.raises(ValueError, match="Multi-stop transport"):
        build_task_groups(q3, illegal, 2, "replicate_relay")


def test_touching_intervals_and_optimal_coloring():
    rows = (ResourceInterval("a", "a", "K2-G1", "A_UAV", 0, 10, "U1"),
            ResourceInterval("b", "b", "K2-G1", "A_UAV", 10, 20, "U2"))
    assert maximum_overlap(rows).required == 1
    assert len({a.internal_resource_id for a in color_intervals(rows, "K2-G1-A-U")}) == 1
    rows += (ResourceInterval("c", "c", "K2-G1", "A_UAV", 5, 15, "U3"),)
    assert maximum_overlap(rows).required == 2
    assert len({a.internal_resource_id for a in color_intervals(rows, "K2-G1-A-U")}) == 2


def test_battery_includes_full_recharge(context):
    scenario, q3, _, _ = context
    examples = [s for s in q3.transport.sorties if s.spec.model_id == "A"][:2]
    assert len(examples) == 2
    fixed = (replace(examples[0], preparation_start_s=0, return_o01_time_s=10,
                     return_soc=.5),
             replace(examples[1], preparation_start_s=10, return_o01_time_s=20,
                     return_soc=.5))
    toy = replace(q3, transport=replace(q3.transport, sorties=fixed))
    group = TaskGroup("K2-G1", ("S001",), tuple(s.spec.sortie_id for s in fixed), ())
    intervals = task_intervals(scenario, toy, group)
    assert maximum_overlap(intervals["A_UAV"]).required == 1
    assert maximum_overlap(intervals["A_battery"]).required == 2


def test_relay_turnaround_and_component_recharge(context):
    scenario, q3, _, _ = context
    relay = q3.relays[0]
    group = TaskGroup("K2-G1", ("S001",), (),
                      ({"task_id": relay.sortie_id,
                        "source_sortie_id": relay.sortie_id},))
    intervals = task_intervals(scenario, q3, group)
    assert intervals["relay_UAV"][0].end_s == relay.turnaround_end_s
    assert relay.turnaround_end_s - relay.return_o01_s == pytest.approx(300)
    assert intervals["relay_component"][0].end_s == pytest.approx(relay.charge_end_s)
    assert intervals["relay_component"][0].end_s > relay.return_o01_s


def test_strict_binding_rejects_split_and_replicate_copies(context):
    scenario, q3, solution, _ = context
    atoms = solution.transport_components
    split = next(p for p in enumerate_partitions(atoms, 2)
                 if any(len({next(i for i, g in enumerate(p) if sid in g)
                             for sid in binding["service_areas"]}) > 1
                        for binding in solution.relay_bindings))
    with pytest.raises(ValueError, match="Strict relay"):
        build_task_groups(q3, split, 2, "strict_no_duplication")
    groups = build_task_groups(q3, split, 2, "replicate_relay")
    copies = [item for group in groups for item in group.relay_tasks]
    assert len(copies) > len(q3.relays)
    by_source = {r.sortie_id: asdict(r) for r in q3.relays}
    assert all(item["snapshot"] == by_source[item["source_sortie_id"]]
               for item in copies)
    assert all(item["task_id"].startswith(item["group_id"] + "-")
               for item in copies)


def test_group_resource_isolation_and_gap(context):
    scenario, q3, solution, _ = context
    selected = next(c for c in solution.policy_results[0].candidates if c.selected)
    ids_by_group = [{a.internal_resource_id for a in g.assignments}
                    for g in selected.groups]
    assert ids_by_group[0].isdisjoint(ids_by_group[1])
    from relief_uav.q4.objectives import partition_metrics
    low_stock = {key: 0 for key in solution.stock}
    score = partition_metrics(selected.groups, low_stock, solution.global_q3_minimum)
    assert score["stock_gap"] == selected.resource_vector
    assert score["stock_gap_total"] == selected.resource_scale


def test_saved_candidate_mutations_fail(context):
    scenario, q3, _, payload = context
    alterations = [
        lambda p: p["policy_results"][0]["candidates"][0]["groups"][0][
            "assignments"][0].__setitem__("occupied_end_s", -1),
        lambda p: p["policy_results"][0]["candidates"][0]["groups"][0][
            "group"]["relay_tasks"][0]["snapshot"].__setitem__("return_soc", -1)
            if p["policy_results"][0]["candidates"][0]["groups"][0][
                "group"]["relay_tasks"] else p["policy_results"][0][
                "candidates"][0]["groups"][1]["group"]["relay_tasks"][0][
                    "snapshot"].__setitem__("return_soc", -1),
        lambda p: p["policy_results"][0]["candidates"][0].__setitem__(
            "stock_gap_total", 999),
        lambda p: p["policy_results"][0]["candidates"][0].__setitem__(
            "selected", not p["policy_results"][0]["candidates"][0]["selected"]),
    ]
    for change in alterations:
        broken = json.loads(json.dumps(payload))
        change(broken)
        assert not validate_q4(scenario, q3, Q3_PATH, broken, q3_passed=True).passed


@pytest.mark.parametrize("field,mutate", [
    ("route", lambda p: p["source_q3_frozen"]["transport"][0]["route"].__setitem__(
        1, "S015")),
    ("transport time", lambda p: p["source_q3_frozen"]["transport"][0].__setitem__(
        "takeoff_time_s", -1)),
    ("relay hover", lambda p: p["source_q3_frozen"]["relays"][0]["hover"].__setitem__(
        "longitude_deg", 0)),
    ("relay service", lambda p: p["source_q3_frozen"]["relays"][0].__setitem__(
        "service_end_s", -1)),
])
def test_q3_immutable_fields_detected(context, field, mutate):
    scenario, q3, _, payload = context
    changed = json.loads(json.dumps(payload))
    mutate(changed)
    validation = validate_q4(scenario, q3, Q3_PATH, changed, q3_passed=True)
    assert not validation.passed, field
    assert any("frozen Q3" in issue for issue in validation.issues)


def test_saved_solution_validator(context):
    scenario, q3, _, payload = context
    validation = validate_q4(scenario, q3, Q3_PATH, payload, q3_passed=True)
    assert validation.passed, validation.issues
    saved = ROOT / "outputs/q4/q4_summary.json"
    if saved.is_file():
        official = ROOT / "outputs/q4/结果提交_Q4.xlsx"
        checked = validate_q4(scenario, q3, Q3_PATH,
            json.loads(saved.read_text(encoding="utf-8")),
            q3_passed=True, official_template_path=official)
        assert checked.passed, checked.issues
