"""Five-axis nondominance over independently validated Q3 snapshots."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts"))

from openpyxl import load_workbook
from q3_q4_improvement import OUT, sheet


def records(path):
    rows = list(load_workbook(path, read_only=True, data_only=True).active.values)
    return [dict(zip(rows[0], row)) for row in rows[1:]]


def main():
    data = []
    for r in records(OUT/"q3_candidate_comparison.xlsx"):
        if r["status"] == "PASS":
            data.append((r["name"], r["j1"], r["j2"], r["j3"],
                         r["transport_sorties"], r["relay_sorties"],
                         r["k2_strict_components"], r["k2_strict_gap"],
                         r["k2_strict_cv"], r["k3_strict_gap"],
                         r["k3_strict_cv"]))
    for r in records(OUT/"q3_route_refinement.xlsx"):
        if r["status"] == "PASS@4s":
            data.append((r["candidate"], r["J1"], r["J2_s"], r["J3_kwh"],
                         26, r["relay_sorties"], r["strict_components"],
                         r["K2_gap"], r["K2_CV"], r["K3_gap"], r["K3_CV"]))
    rows = []
    for row in data:
        x = row[1:6]
        dominated_by = []
        for other in data:
            if other is row:
                continue
            y = other[1:6]
            if all(a <= b+1e-7 for a, b in zip(y, x)) and any(
                    a < b-1e-7 for a, b in zip(y, x)):
                dominated_by.append(other[0])
        rows.append(row+(not dominated_by, ",".join(dominated_by)))
    sheet("q3_multiobjective_pareto.xlsx", ["candidate", "J1", "J2_s",
          "J3_kwh", "J4_transport", "J4_relay", "strict_components",
          "K2_gap", "K2_CV", "K3_gap", "K3_CV",
          "non_dominated_J1_J2_J3_J4t_J4r", "dominated_by"], rows)
    print("validated", len(rows), "nondominated", sum(r[-2] for r in rows), flush=True)


if __name__ == "__main__":
    main()
