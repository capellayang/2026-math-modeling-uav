"""Derive indivisible Q4 tasks from the saved Q3 schedule."""

from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

from relief_uav.data.models import Scenario
from relief_uav.q3.model import Q3Solution
from .model import AtomicTaskComponent


def source_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def frozen_q3_projection(q3: Q3Solution) -> dict:
    """All decisions relevant to Q4 are copied; validator compares to source."""
    return {
        "transport": [{"sortie_id": s.spec.sortie_id, "model_id": s.spec.model_id,
                       "route": list(s.spec.route),
                       "deliveries": [asdict(d) for d in s.spec.deliveries],
                       "preparation_start_s": s.preparation_start_s,
                       "takeoff_time_s": s.takeoff_time_s,
                       "legs": [asdict(leg) for leg in s.legs],
                       "stops": [asdict(stop) for stop in s.stops],
                       "return_o01_time_s": s.return_o01_time_s,
                       "energy_kwh": s.energy_kwh, "return_soc": s.return_soc}
                      for s in q3.transport.sorties],
        "relays": [asdict(r) for r in q3.relays],
        "communication": [asdict(c) for c in q3.communication],
    }


class _UnionFind:
    def __init__(self, items):
        self.parent = {item: item for item in items}

    def find(self, item):
        if self.parent[item] != item:
            self.parent[item] = self.find(self.parent[item])
        return self.parent[item]

    def union(self, a, b):
        pa, pb = self.find(a), self.find(b)
        if pa != pb:
            self.parent[max(pa, pb)] = min(pa, pb)

    def components(self):
        groups = {}
        for item in self.parent:
            groups.setdefault(self.find(item), []).append(item)
        return tuple(tuple(sorted(x)) for x in sorted(groups.values(), key=lambda x: min(x)))


def relay_bindings(q3: Q3Solution) -> tuple[dict, ...]:
    by_sortie = {s.spec.sortie_id: s for s in q3.transport.sorties}
    bindings = []
    for relay in sorted(q3.relays, key=lambda r: r.sortie_id):
        tids = sorted({c.transport_sortie_id for c in q3.communication
                       if c.mode == "RELAY" and c.relay_sortie_id == relay.sortie_id})
        if not tids:
            raise ValueError(f"Orphan Q3 relay task: {relay.sortie_id}")
        if any(t not in by_sortie for t in tids):
            raise ValueError(f"Relay {relay.sortie_id} references unknown transport")
        bindings.append({"relay_sortie_id": relay.sortie_id,
                         "transport_sorties": tids,
                         "service_areas": sorted({n for tid in tids
                            for n in by_sortie[tid].spec.route if n != "O01"})})
    return tuple(bindings)


def atomic_components(scenario: Scenario, q3: Q3Solution,
                      strict: bool) -> tuple[AtomicTaskComponent, ...]:
    uf = _UnionFind(scenario.services)
    for sortie in q3.transport.sorties:
        sites = [n for n in sortie.spec.route if n != "O01"]
        if not sites or any(n not in scenario.services for n in sites):
            raise ValueError(f"Invalid Q3 route: {sortie.spec.sortie_id}")
        for site in sites[1:]:
            uf.union(sites[0], site)
    bindings = relay_bindings(q3)
    if strict:
        for binding in bindings:
            sites = binding["service_areas"]
            for site in sites[1:]:
                uf.union(sites[0], site)
    result = []
    for index, sites in enumerate(uf.components(), 1):
        site_set = set(sites)
        transports = tuple(sorted(s.spec.sortie_id for s in q3.transport.sorties
            if any(n in site_set for n in s.spec.route if n != "O01")))
        relays = tuple(sorted(b["relay_sortie_id"] for b in bindings
            if set(b["service_areas"]) & site_set))
        boxes = [b for b in scenario.boxes.values() if b.service_id in site_set]
        result.append(AtomicTaskComponent(f"C{index}", sites, transports,
                                          relays, len(boxes),
                                          sum(b.mass_kg for b in boxes)))
    return tuple(result)
