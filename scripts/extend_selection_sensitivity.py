"""Complete integer Gap/Scale epsilon grid on the exact Q4 candidate sets."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts"))
from q3_q4_improvement import OUT, sheet


def grid(name, q4):
    rows = []
    for policy in q4["policy_results"]:
        if not policy["feasible"]:
            continue
        cs = policy["candidates"]
        min_gap = min(c["stock_gap_total"] for c in cs)
        max_gap = max(c["stock_gap_total"] for c in cs)
        for dg in range(max_gap-min_gap+1):
            gap_ok = [c for c in cs if c["stock_gap_total"] <= min_gap+dg]
            min_scale = min(c["resource_scale"] for c in gap_ok)
            max_scale = max(c["resource_scale"] for c in gap_ok)
            for ds in range(max_scale-min_scale+1):
                eligible = [c for c in gap_ok if c["resource_scale"] <= min_scale+ds]
                x = min(eligible, key=lambda c: (c["workload_cv"],
                    c["stock_gap_total"], c["resource_scale"],
                    c["canonical_group_signature"]))
                rows.append((name, policy["relay_policy"], policy["k"],
                    "balance_epsilon", dg, ds, x["candidate_id"],
                    x["stock_gap_total"], x["resource_scale"],
                    x["workload_cv"], x["canonical_group_signature"]))
        front = [c for c in cs if c["pareto"]]
        keys = ("stock_gap_total", "resource_scale", "workload_cv")
        lo = [min(c[key] for c in cs) for key in keys]
        hi = [max(c[key] for c in cs) for key in keys]
        def distance(c):
            return sum(((c[key]-a)/(b-a) if b>a else 0)**2
                       for key, a, b in zip(keys, lo, hi))**.5
        knee = min(front, key=lambda c: (distance(c), c["canonical_group_signature"]))
        rows.append((name, policy["relay_policy"], policy["k"],
            "normalized_ideal_knee", None, None, knee["candidate_id"],
            knee["stock_gap_total"], knee["resource_scale"],
            knee["workload_cv"], knee["canonical_group_signature"]))
        chosen = next(c for c in cs if c["selected"])
        rows.append((name, policy["relay_policy"], policy["k"],
            "current_lexicographic", 0, 0, chosen["candidate_id"],
            chosen["stock_gap_total"], chosen["resource_scale"],
            chosen["workload_cv"], chosen["canonical_group_signature"]))
    return rows


def main():
    names = [("CURRENT", ROOT/"outputs/q4/q4_summary.json")]
    names += [(p.name.removesuffix("_q4.json").upper(), p)
              for p in OUT.glob("*_q4.json") if p.name != "current_q4.json"]
    rows = []
    for name, path in names:
        q4 = json.loads(path.read_text(encoding="utf-8"))
        rows.extend(grid(name, q4))
    sheet("q4_selection_sensitivity.xlsx", ["q3", "policy", "K",
          "selection_rule", "delta_gap", "delta_scale", "candidate",
          "gap", "scale", "CV", "partition"], rows)
    print("epsilon_grid_rows", len(rows), "q3_snapshots", len(names), flush=True)


if __name__ == "__main__":
    main()
