"""Coarse-to-fine relay hover candidates driven by Q2 blackout geometry."""

from dataclasses import asdict
from hashlib import sha256
import json
from math import hypot
from pathlib import Path

from relief_uav.communication.coverage import RadioEnvironment
from relief_uav.communication.model import CommunicationEndpoint
from relief_uav.communication.trajectory import transport_phases
from relief_uav.q2.model import Q2Solution
from .model import Q3AlgorithmConfig, RelayHoverPoint
from .relay_physics import hover_point, relay_travel


def blackout_representatives(scenario, transport: Q2Solution, audit: dict):
    by_id = {s.spec.sortie_id: s for s in transport.sorties}
    phases = {sid: {p.name: p for p in transport_phases(scenario, sortie)}
              for sid, sortie in by_id.items()}
    points = []
    for row in audit["intervals"]:
        phase = phases[row["transport_sortie_id"]][row["phase"]]
        for fraction in (0.1, 0.5, 0.9):
            t = row["start_s"]+fraction*(row["end_s"]-row["start_s"])
            points.append((row["transport_sortie_id"], t, phase.at(t)))
    return tuple(points)


def _xy_pool(env: RadioEnvironment, reps, grid_m: float,
             mode: str = "local"):
    # Local node rectangle, not the full regional DEM. Candidate sources are
    # services, blackout positions, spatial midpoints and a coarse grid.
    nodes = tuple(env.scenario.services.values())
    points = [(n.longitude_deg, n.latitude_deg) for n in nodes]
    points += [(p.longitude_deg, p.latitude_deg) for _, _, p in reps]
    gateway = env.gateway
    points += [((p.longitude_deg+gateway.longitude_deg)/2,
                (p.latitude_deg+gateway.latitude_deg)/2) for _, _, p in reps]
    if mode not in ("local", "buffered", "full_dem"):
        raise ValueError(f"Unknown hover XY mode: {mode}")
    buffer = .006 if mode == "local" else .025
    west = min(n.longitude_deg for n in nodes)-buffer
    east = max(n.longitude_deg for n in nodes)+buffer
    south = min(n.latitude_deg for n in nodes)-buffer
    north = max(n.latitude_deg for n in nodes)+buffer
    if mode == "full_dem":
        bounds = env.dem.bounds
        west, east = bounds.left, bounds.right
        south, north = bounds.bottom, bounds.top
    lon_step = grid_m/102_500
    lat_step = grid_m/110_700
    x = west
    while x <= east:
        y = south
        while y <= north:
            points.append((x, y))
            y += lat_step
        x += lon_step
    # Quantize only candidate generation, not radio or final coordinates.
    return tuple(dict.fromkeys((round(x, 7), round(y, 7)) for x, y in points))


def generate_candidates(env: RadioEnvironment, transport: Q2Solution,
                        audit: dict, config: Q3AlgorithmConfig,
                        cache_path: Path | None = None):
    reps = blackout_representatives(env.scenario, transport, audit)
    dem_stat = env.dem.path.stat()
    key = sha256(json.dumps({"algorithm_version": 4,
                             "dem": (dem_stat.st_size, dem_stat.st_mtime_ns),
                             "communication": asdict(env.params),
                             "sorties": [(s.spec.route, s.takeoff_time_s)
                                           for s in transport.sorties],
                             "audit": [(x["transport_sortie_id"], x["start_s"], x["end_s"])
                                       for x in audit["intervals"]],
                             "grid": config.hover_grid_m,
                             "altitudes": config.hover_altitudes_m,
                             "top_k": config.hover_top_k,
                             "xy_mode": config.hover_xy_mode,
                             "altitude_mode": config.hover_altitude_mode},
                            sort_keys=True).encode()).hexdigest()
    if cache_path and cache_path.is_file():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if payload.get("fingerprint") == key:
            return tuple(RelayHoverPoint(**row) for row in payload["points"]), reps, True
    model = next(iter(env.scenario.relay_models.values()))
    raw = []
    for longitude, latitude in _xy_pool(env, reps, config.hover_grid_m,
                                        config.hover_xy_mode):
        altitudes = ((max(config.hover_altitudes_m),)
                     if config.hover_altitude_mode == "legacy"
                     else config.hover_altitudes_m)
        for agl in altitudes:
            try:
                point = hover_point(env.dem, longitude, latitude,
                                agl, model.max_hover_agl_m)
                endpoint = CommunicationEndpoint(point.longitude_deg, point.latitude_deg,
                                             point.hover_msl_m)
                backhaul = env.link(endpoint, env.gateway, "backhaul")
                if not backhaul.available:
                    continue
                travel = relay_travel(env.scenario, env.dem, point)
                if travel.outbound_energy_kwh+travel.return_energy_kwh >= (
                    model.usable_energy_kwh*(1-model.minimum_return_soc)):
                    continue
                access = [(index, env.link(position, endpoint, "access"))
                          for index, (_, _, position) in enumerate(reps)]
                covered = frozenset(index for index, link in access if link.available)
                if covered:
                    raw.append((point, covered, backhaul.margin_db,
                            travel.outbound_energy_kwh+travel.return_energy_kwh,
                            min(link.margin_db for _, link in access if link.available),
                            travel.outbound_s+travel.return_s))
            except ValueError:
                continue
    # Set-cover diversity first, then high-coverage score; include fringe points.
    selected, uncovered = [], set(range(len(reps)))
    remaining = list(raw)
    while uncovered and remaining and len(selected) < config.hover_top_k:
        best = max(remaining, key=lambda r: (len(r[1] & uncovered),
                                             len(r[1]), r[4], r[2], -r[3], -r[5]))
        if not best[1] & uncovered:
            break
        selected.append(best)
        uncovered -= best[1]
        remaining.remove(best)
    core_count = len(selected)
    remaining.sort(key=lambda r: (len(r[1]), r[4], r[2], -r[3], -r[5]), reverse=True)
    for row in remaining:
        if len(selected) >= config.hover_top_k:
            break
        if any(row[1] == old[1] and hypot(
                (row[0].longitude_deg-old[0].longitude_deg)*102_500,
                (row[0].latitude_deg-old[0].latitude_deg)*110_700) < 200
                for old in selected):
            continue
        selected.append(row)
    # Refine the best coarse sites locally in XY and AGL, retaining only points
    # whose coverage includes the original set. This cannot lose a rare fringe
    # requirement already covered by the coarse set-cover stage.
    refined = []
    for index in range(min(6, core_count)):
        base = selected[index]
        best = base
        x0, y0 = base[0].longitude_deg, base[0].latitude_deg
        offsets = ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1),
                   (1, 1), (1, -1), (-1, 1), (-1, -1))
        for ox, oy in offsets:
            for agl in (base[0].hover_agl_m,
                        max(1.0, base[0].hover_agl_m-25.0)):
                longitude = x0 + ox*config.hover_grid_m/(4*102_500)
                latitude = y0 + oy*config.hover_grid_m/(4*110_700)
                try:
                    point = hover_point(env.dem, longitude, latitude, agl,
                                        model.max_hover_agl_m)
                    endpoint = CommunicationEndpoint(point.longitude_deg,
                                                     point.latitude_deg,
                                                     point.hover_msl_m)
                    backhaul = env.link(endpoint, env.gateway, "backhaul")
                    if not backhaul.available:
                        continue
                    travel = relay_travel(env.scenario, env.dem, point)
                    energy = travel.outbound_energy_kwh+travel.return_energy_kwh
                    if energy >= model.usable_energy_kwh*(1-model.minimum_return_soc):
                        continue
                    access = [(i, env.link(position, endpoint, "access"))
                              for i, (_, _, position) in enumerate(reps)]
                    covered = frozenset(i for i, link in access if link.available)
                    if base[1].issubset(covered):
                        candidate = (point, covered, backhaul.margin_db, energy,
                                     min(link.margin_db for _, link in access if link.available),
                                     travel.outbound_s+travel.return_s)
                        if (len(covered), backhaul.margin_db, -energy) > (
                                len(best[1]), best[2], -best[3]):
                            best = candidate
                except ValueError:
                    continue
        if best is not base:
            refined.append(best)
    # Preserve every coarse set-cover point. Fine variants replace only
    # redundant high-score extras and cannot remove the original coverage.
    refined = refined[:max(0, config.hover_top_k-core_count)]
    selected = (selected[:core_count] + refined +
                selected[core_count:core_count+max(0,config.hover_top_k-core_count-len(refined))])
    points = tuple(row[0] for row in selected)
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps({"fingerprint": key, "points": [asdict(p)
            for p in points], "representative_count": len(reps),
            "uncovered_representatives": len(uncovered), "raw_candidate_count": len(raw),
            "locally_refined_count": len(refined)},
            ensure_ascii=False, indent=2), encoding="utf-8")
    return points, reps, False
