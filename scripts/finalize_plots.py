"""Scientific trade-off figures from validated experiment workbooks and exact Q4 sets."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts"))
from q3_q4_improvement import OUT, FIG

from openpyxl import load_workbook
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def records(path):
    rows = list(load_workbook(path, read_only=True, data_only=True).active.values)
    return [dict(zip(rows[0], row)) for row in rows[1:]]


def main():
    FIG.mkdir(parents=True, exist_ok=True)
    main_rows = [r for r in records(OUT/"q3_candidate_comparison.xlsx")
                 if r["status"] == "PASS"]
    routes = [r for r in records(OUT/"q3_route_refinement.xlsx")
              if r["status"] == "PASS@4s"]
    points = [{"name": r["name"], "j1": r["j1"], "j2": r["j2"],
               "j3": r["j3"], "relay": r["relay_sorties"],
               "k2_gap": r["k2_strict_gap"], "k2_cv": r["k2_strict_cv"]}
              for r in main_rows]
    points += [{"name": r["candidate"], "j1": r["J1"],
                "j2": r["J2_s"], "j3": r["J3_kwh"],
                "relay": r["relay_sorties"], "k2_gap": r["K2_gap"],
                "k2_cv": r["K2_CV"]} for r in routes]
    labels = {"CURRENT", "COMPONENT_REUSE_8", "MULTIOBJECTIVE_ENERGY",
              "MULTIOBJECTIVE_SORTIES", "ROUTE_SCALAR_00",
              "ROUTE_MULTIOBJECTIVE_03", "ROUTE_MULTIOBJECTIVE_04"}
    offsets = {"CURRENT": (6, 14), "COMPONENT_REUSE_8": (6, -16),
               "MULTIOBJECTIVE_ENERGY": (6, 12),
               "MULTIOBJECTIVE_SORTIES": (6, -14),
               "ROUTE_SCALAR_00": (6, 2),
               "ROUTE_MULTIOBJECTIVE_03": (6, 2),
               "ROUTE_MULTIOBJECTIVE_04": (6, 2)}
    fig, ax = plt.subplots(figsize=(10, 6))
    for p in points:
        color = "tab:blue" if p["name"] == "CURRENT" else (
            "tab:green" if p["name"].startswith("ROUTE") else "tab:orange")
        ax.scatter(p["j2"]/3600, p["j3"], s=25+25*p["relay"],
                   c=color, alpha=.68, edgecolor="black", linewidth=.4)
        if p["name"] in labels:
            ax.annotate(p["name"], (p["j2"]/3600, p["j3"]),
                        xytext=offsets[p["name"]], textcoords="offset points", fontsize=7)
    ax.set(xlabel="Q3 joint makespan (hours)", ylabel="Q3 total energy (kWh)",
           title="Validated Q3 candidates: J1 = 0; marker size = relay sorties")
    ax.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(FIG/"q3_pareto_extended.png", dpi=180); plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 6))
    scatter = ax.scatter([p["j2"]/3600 for p in points],
                         [p["k2_cv"] for p in points],
                         c=[p["k2_gap"] for p in points], cmap="viridis",
                         s=[35+30*p["relay"] for p in points],
                         edgecolor="black", linewidth=.4)
    for p in points:
        if p["name"] in labels:
            ax.annotate(p["name"], (p["j2"]/3600, p["k2_cv"]),
                        xytext=offsets[p["name"]], textcoords="offset points", fontsize=7)
    fig.colorbar(scatter, ax=ax, label="Q4 strict K2 stock gap")
    ax.set(xlabel="Q3 joint makespan (hours)", ylabel="Q4 strict K2 workload CV",
           title="Upstream Q3 quality versus downstream strict partition balance")
    ax.grid(alpha=.3)
    fig.tight_layout(); fig.savefig(FIG/"q3_vs_q4_tradeoff.png", dpi=180); plt.close(fig)

    formal = json.loads((ROOT/"outputs/q4/q4_summary.json").read_text(encoding="utf-8"))
    for k in (2, 3):
        fig, ax = plt.subplots(figsize=(9, 6))
        for policy, color in (("strict_no_duplication", "tab:blue"),
                              ("replicate_relay", "tab:orange")):
            part = next(r for r in formal["policy_results"]
                        if r["k"] == k and r["relay_policy"] == policy)
            cs = part["candidates"]
            ax.scatter([c["stock_gap_total"] for c in cs],
                       [c["workload_cv"] for c in cs],
                       s=[4*c["resource_scale"] for c in cs],
                       c=color, alpha=.45, label=policy,
                       edgecolor="black", linewidth=.3)
            chosen = next(c for c in cs if c["selected"])
            ax.scatter([chosen["stock_gap_total"]], [chosen["workload_cv"]],
                       s=4*chosen["resource_scale"], c=color, marker="*",
                       edgecolor="black", linewidth=.8)
        ax.set(xlabel="Q4 stock gap (resource units)", ylabel="Workload CV",
               title=f"Q4 K={k}: all exact partitions; marker size = resource scale")
        ax.legend(); ax.grid(alpha=.3)
        fig.tight_layout(); fig.savefig(FIG/f"q4_k{k}_tradeoff.png", dpi=180)
        plt.close(fig)
    print(FIG, flush=True)


if __name__ == "__main__":
    main()
