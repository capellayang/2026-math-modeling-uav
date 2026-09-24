"""Add relay count, energy and component demand to all Q4 policy rows."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts"))
from openpyxl import load_workbook
from q3_q4_improvement import OUT, sheet


def main():
    path = OUT/"q4_relay_policy_comparison.xlsx"
    values = list(load_workbook(path, read_only=True, data_only=True).active.values)
    headers = list(values[0])
    rows = []
    q4_cache = {}
    for raw in values[1:]:
        row = list(raw)
        name, policy, k = row[:3]
        if policy in ("strict_no_duplication", "replicate_relay"):
            if name not in q4_cache:
                source = (ROOT/"outputs/q4/q4_summary.json" if name == "CURRENT"
                          else OUT/f"{name.lower()}_q4.json")
                q4_cache[name] = json.loads(source.read_text(encoding="utf-8"))
            result = next(r for r in q4_cache[name]["policy_results"]
                          if r["relay_policy"] == policy and r["k"] == k)
            chosen = next(c for c in result["candidates"] if c["selected"])
            row[9] = chosen["effective_relay_task_count"]
            row[10] = sum(t["snapshot"]["total_energy_kwh"]
                for g in chosen["groups"] for t in g["group"]["relay_tasks"])
            row[11] = chosen["resource_vector"]["relay_component"]
            row[12] = len(result["candidates"])
        rows.append(row)
    sheet("q4_relay_policy_comparison.xlsx", headers, rows)
    print("rows", len(rows), flush=True)


if __name__ == "__main__":
    main()
