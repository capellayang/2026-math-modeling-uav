"""Check the five-seed comparison's Q3 quality gate and Q4 tie-break."""

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "q3_five_seed_batch.py"
spec = importlib.util.spec_from_file_location("q3_five_seed_batch", SCRIPT)
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


def _row(seed, j2, j3, components, largest, *, j1=0):
    return {"seed": seed, "status": "PASS", "j1": j1, "j2_s": j2,
            "j3_kwh": j3, "nt": 26, "nr": 5,
            "components": components, "largest_component": largest,
            "k2": {"legal_partitions": 3, "gap": 2, "cv": .9},
            "k3": {"legal_partitions": 1, "gap": 5, "cv": 1.19},
            "pareto": False, "near_pareto": False, "final_choice": False}


def test_q3_gate_precedes_q4_structure():
    rows = [_row(1, 100, 80, 3, 13),
            _row(2, 101, 80.5, 5, 7),
            _row(3, 105, 80, 6, 4),
            _row(4, 100, 82, 6, 4, j1=1)]
    batch._rank(rows)
    assert rows[1]["near_pareto"] and rows[1]["final_choice"]
    assert not rows[2]["near_pareto"]  # 5% slower than best J2
    assert not rows[3]["near_pareto"]  # nonzero J1
    assert sum(bool(row["final_choice"]) for row in rows) == 1


def test_failed_seed_never_sets_comparison_best():
    rows = [_row(1, 100, 80, 3, 13),
            {**_row(2, 1, 1, 6, 2), "status": "FAIL"}]
    batch._rank(rows)
    assert rows[0]["near_pareto"] and rows[0]["final_choice"]
    assert not rows[1]["near_pareto"] and not rows[1]["final_choice"]


def test_recompute_clears_previous_checkpoint_choice():
    rows = [_row(1, 100, 80, 3, 13)]
    batch._rank(rows)
    assert rows[0]["final_choice"]
    rows.append(_row(2, 90, 78, 3, 13))
    batch._rank(rows)
    assert not rows[0]["final_choice"]
    assert rows[1]["final_choice"]
