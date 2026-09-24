"""Join route-refined Q3 candidates into the common cross-Q3 Q4 workbook."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts"))
from openpyxl import load_workbook
from q3_q4_improvement import OUT, sheet


def main():
    path = OUT/"q4_cross_q3_comparison.xlsx"
    values = list(load_workbook(path, read_only=True, data_only=True).active.values)
    rows = [tuple(x) for x in values[1:] if not str(x[0]).startswith("ROUTE_")]
    for source in sorted(OUT.glob("route_*_q4.json")):
        name = source.name.removesuffix("_q4.json").upper()
        q4 = json.loads(source.read_text(encoding="utf-8"))
        for policy in q4["policy_results"]:
            if not policy["feasible"]:
                continue
            chosen = next(c for c in policy["candidates"] if c["selected"])
            rows.append((name, policy["relay_policy"], policy["k"],
                policy["atomic_component_count"], chosen["stock_gap_total"],
                chosen["resource_scale"], chosen["workload_cv"],
                chosen["candidate_id"], "PASS@4s"))
    sheet("q4_cross_q3_comparison.xlsx", list(values[0]), rows)
    print("rows", len(rows), flush=True)


if __name__ == "__main__":
    main()
