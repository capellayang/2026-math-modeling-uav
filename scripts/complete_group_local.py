"""Run the third, clearly experimental Q4 relay ownership interpretation."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
sys.path.insert(0, str(ROOT/"scripts"))

from openpyxl import load_workbook

from q3_q4_improvement import OUT, group_local_policy, save_json, sheet
from relief_uav.communication.coverage import RadioEnvironment
from relief_uav.data import load_scenario
from relief_uav.geo.dem import DigitalElevationModel
from relief_uav.geo.segments import dem_source_path
from relief_uav.q3.report import load_q3_solution
from relief_uav.q4.inheritance import atomic_components


def main():
    scenario = load_scenario(ROOT)
    env = RadioEnvironment(scenario, DigitalElevationModel(dem_source_path(ROOT)))
    candidates = [("CURRENT", ROOT/"outputs/q3/q3_summary.json")]
    rows = []
    candidate_rows = []
    for name, path in candidates:
        q3 = load_q3_solution(path)
        for k in (2, 3):
            result = group_local_policy(scenario, env, q3, k)
            if result is None:
                continue
            save_json(OUT/f"{name.lower()}_group_local_k{k}.json", result)
            rows.append((name, "group_local_relay", k,
                         len(atomic_components(scenario, q3, strict=False)),
                         result["gap"], result["scale"], result["cv"],
                         result["partition"], result["validator"],
                         result["relay_sorties"], result["relay_energy_kwh"],
                         result["relay_components"], result["enumerated"]))
            candidate_rows.extend((name, k, x["partition"], x["gap"],
                x["scale"], x["cv"], x["relay_sorties"],
                x["relay_energy_kwh"], x["relay_components"], x["selected"])
                for x in result["candidates"])
            print(name, "K", k, result["validator"],
                  result["gap"], round(result["cv"], 4), flush=True)
            delta_gap, delta_scale = (1, 1) if k == 2 else (3, 5)
            balanced = group_local_policy(scenario, env, q3, k,
                delta_gap=delta_gap, delta_scale=delta_scale)
            if balanced:
                save_json(OUT/f"{name.lower()}_group_local_k{k}_balanced.json", balanced)
                rows.append((name, "group_local_relay_balanced", k,
                    len(atomic_components(scenario, q3, strict=False)),
                    balanced["gap"], balanced["scale"], balanced["cv"],
                    balanced["partition"], balanced["validator"],
                    balanced["relay_sorties"], balanced["relay_energy_kwh"],
                    balanced["relay_components"], balanced["enumerated"]))
                print(name, "K", k, "balanced", balanced["validator"],
                      balanced["gap"], round(balanced["cv"], 4), flush=True)
    book = load_workbook(OUT/"q4_relay_policy_comparison.xlsx", read_only=True)
    old = list(book.active.values)
    sheet("q4_relay_policy_comparison.xlsx", list(old[0]),
          [tuple(row) for row in old[1:] if not str(row[1]).startswith("group_local_relay")]+rows)
    sheet("q4_group_local_candidates.xlsx", ["q3", "K", "partition",
          "gap", "scale", "CV", "relay_sorties", "relay_energy_kwh",
          "relay_components", "selected_current_lexicographic"], candidate_rows)


if __name__ == "__main__":
    main()
