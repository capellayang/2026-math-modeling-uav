"""CV/range sensitivity across each transparent Q4 workload dimension."""

from collections import defaultdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts"))

from openpyxl import load_workbook
from q3_q4_improvement import OUT, sheet


def main():
    book = load_workbook(OUT/"q4_workload_sensitivity.xlsx", read_only=True,
                         data_only=True)
    rows = list(book.active.values)
    header = rows[0]
    grouped = defaultdict(list)
    for row in rows[1:]:
        grouped[row[:3]].append(dict(zip(header, row)))
    metrics = ["service_areas", "boxes", "cargo_kg", "transport_sorties",
               "transport_operation_s", "relay_service_s", "relay_mission_s",
               "project_workload_s", "transport_UAV_hours",
               "transport_battery_hours", "relay_UAV_hours", "relay_component_hours"]
    summary = []
    for (q3, policy, k), groups in grouped.items():
        for metric in metrics:
            vals = [float(g[metric] or 0) for g in groups]
            mean = sum(vals)/len(vals)
            cv = ((sum((x-mean)**2 for x in vals)/len(vals))**.5/mean
                  if mean else None)
            normalized_range = ((max(vals)-min(vals))/mean if mean else None)
            summary.append((q3, policy, k, metric, min(vals), max(vals),
                            mean, cv, normalized_range,
                            max(vals)/sum(vals) if sum(vals) else None))
    sheet("q4_workload_metric_summary.xlsx",
          ["q3", "policy", "K", "metric", "min", "max", "mean", "CV",
           "normalized_range", "largest_group_share"], summary)
    for row in summary:
        if row[0] == "CURRENT" and row[1] == "strict_no_duplication" and row[3] in (
                "project_workload_s", "relay_mission_s", "transport_operation_s"):
            print(row, flush=True)


if __name__ == "__main__":
    main()
