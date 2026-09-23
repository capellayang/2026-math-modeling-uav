"""Exact concurrency and optimal interval coloring for fixed Q3 tasks."""

from dataclasses import asdict
from heapq import heappop, heappush

from relief_uav.data.models import Scenario
from relief_uav.physics.battery import charge_to_full_s
from relief_uav.q3.model import Q3Solution
from .model import (RESOURCE_KEYS, Q4GroupMetrics, ResourceAssignment,
                    ResourceInterval, ResourcePeak, TaskGroup)


def maximum_overlap(intervals: tuple[ResourceInterval, ...],
                    group_id: str = "", resource_type: str = "") -> ResourcePeak:
    """End events precede starts at a common instant: intervals are [start,end)."""
    if not intervals:
        return ResourcePeak(group_id, resource_type, None, None, 0, ())
    events = []
    for row in intervals:
        if row.end_s <= row.start_s:
            raise ValueError(f"Nonpositive resource interval: {row.task_id}")
        events.extend(((row.start_s, 1, row.task_id),
                       (row.end_s, 0, row.task_id)))
    events.sort()
    active = set()
    best = ResourcePeak(group_id, resource_type, None, None, 0, ())
    at = 0
    while at < len(events):
        time = events[at][0]
        while at < len(events) and events[at][0] == time and events[at][1] == 0:
            active.remove(events[at][2]); at += 1
        while at < len(events) and events[at][0] == time and events[at][1] == 1:
            active.add(events[at][2]); at += 1
        next_time = events[at][0] if at < len(events) else None
        if next_time is not None and next_time > time and len(active) > best.required:
            best = ResourcePeak(group_id, resource_type, time, next_time,
                                len(active), tuple(sorted(active)))
    return best


def color_intervals(intervals: tuple[ResourceInterval, ...], prefix: str
                    ) -> tuple[ResourceAssignment, ...]:
    ordered = sorted(intervals, key=lambda x: (x.start_s, x.end_s, x.task_id))
    busy = []  # (end, color)
    free = []
    assignments = []
    color_count = 0
    for row in ordered:
        while busy and busy[0][0] <= row.start_s:
            _, released = heappop(busy)
            heappush(free, released)
        if free:
            color = heappop(free)
        else:
            color_count += 1
            color = color_count
        heappush(busy, (row.end_s, color))
        assignments.append(ResourceAssignment(
            row.task_id, row.source_task_id, row.group_id, row.resource_type,
            f"{prefix}-{color:02d}", row.start_s, row.end_s,
            row.source_q3_resource_id))
    return tuple(assignments)


def stock_vector(scenario: Scenario) -> dict[str, int]:
    return {
        **{f"{model}_UAV": sum(x.model_id == model for x in scenario.transport_drones.values())
           for model in "ABC"},
        **{f"{model}_battery": scenario.battery_stocks[model].count for model in "ABC"},
        "relay_UAV": len(scenario.relay_drones),
        "relay_component": sum(x.count for x in scenario.component_stocks.values()),
    }


def task_intervals(scenario: Scenario, q3: Q3Solution, group: TaskGroup
                   ) -> dict[str, tuple[ResourceInterval, ...]]:
    transport = {s.spec.sortie_id: s for s in q3.transport.sorties}
    relay = {r.sortie_id: r for r in q3.relays}
    records: dict[str, list[ResourceInterval]] = {key: [] for key in RESOURCE_KEYS}
    for sid in group.transport_sorties:
        s = transport[sid]
        model = s.spec.model_id
        start = s.preparation_start_s
        end = s.return_o01_time_s
        charge_end = end + charge_to_full_s(
            s.return_soc, scenario.battery_stocks[model].full_charge_s)
        records[f"{model}_UAV"].append(ResourceInterval(sid, sid, group.group_id,
            f"{model}_UAV", start, end, s.drone_id))
        records[f"{model}_battery"].append(ResourceInterval(sid, sid, group.group_id,
            f"{model}_battery", start, charge_end, s.battery_id))
    component = next(iter(scenario.component_stocks.values()))
    for item in group.relay_tasks:
        rid = item["source_sortie_id"]
        task_id = item["task_id"]
        r = relay[rid]
        start = r.preparation_start_s
        charge_end = r.return_o01_s + charge_to_full_s(r.return_soc,
                                                        component.full_charge_s)
        if abs(charge_end - r.charge_end_s) > 1e-5:
            raise ValueError(f"Q3 component charge end disagrees with official charging: {rid}")
        records["relay_UAV"].append(ResourceInterval(task_id, rid, group.group_id,
            "relay_UAV", start, r.turnaround_end_s, r.drone_id))
        records["relay_component"].append(ResourceInterval(task_id, rid,
            group.group_id, "relay_component", start, charge_end, r.component_id))
    return {key: tuple(rows) for key, rows in records.items()}


def group_metrics(scenario: Scenario, q3: Q3Solution,
                  group: TaskGroup) -> Q4GroupMetrics:
    intervals = task_intervals(scenario, q3, group)
    requirements = {}
    peaks = []
    assignments = []
    inherited = {}
    for key in RESOURCE_KEYS:
        rows = intervals[key]
        peak = maximum_overlap(rows, group.group_id, key)
        suffix = key.replace("_UAV", "-U").replace("_battery", "-BAT")
        suffix = suffix.replace("relay", "R").replace("_component", "-COMP")
        colored = color_intervals(rows, f"{group.group_id}-{suffix}")
        if len({a.internal_resource_id for a in colored}) != peak.required:
            raise AssertionError(f"Coloring/overlap mismatch: {group.group_id}/{key}")
        requirements[key] = peak.required
        inherited[key] = len({r.source_q3_resource_id for r in rows})
        peaks.append(peak)
        assignments.extend(colored)
    services = set(group.service_areas)
    boxes = [b for b in scenario.boxes.values() if b.service_id in services]
    transport = {s.spec.sortie_id: s for s in q3.transport.sorties}
    relay = {r.sortie_id: r for r in q3.relays}
    tw = sum(transport[sid].return_o01_time_s - transport[sid].preparation_start_s
             for sid in group.transport_sorties)
    rw = sum(relay[r["source_sortie_id"]].return_o01_s -
             relay[r["source_sortie_id"]].preparation_start_s for r in group.relay_tasks)
    return Q4GroupMetrics(group, len(services), len(boxes),
        sum(b.mass_kg for b in boxes), len(group.transport_sorties),
        len(group.relay_tasks), tw, rw, tw + rw, inherited,
        requirements, tuple(peaks), tuple(assignments))


def global_minimum(scenario: Scenario, q3: Q3Solution) -> dict[str, int]:
    group = TaskGroup("GLOBAL", tuple(sorted(scenario.services)),
        tuple(s.spec.sortie_id for s in q3.transport.sorties),
        tuple({"task_id": r.sortie_id, "source_sortie_id": r.sortie_id,
               "snapshot": asdict(r)} for r in q3.relays))
    return group_metrics(scenario, q3, group).minimum_recolored_requirement
