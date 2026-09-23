"""Rebuild machine-readable Q2-v1/v2 comparison from saved validated outputs."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from relief_uav.data import load_scenario
from relief_uav.geo import build_segment_matrix
from relief_uav.q2.report import load_q2_solution
from relief_uav.q2.report_v2 import benchmark_saved_v2
from relief_uav.validation.q2 import validate_q2


def main() -> None:
    scenario = load_scenario(ROOT)
    segments = build_segment_matrix(scenario)
    for label, path in (
        ("v1", ROOT / "outputs/q2_v2/baseline_v1/q2_summary.json"),
        ("v2", ROOT / "outputs/q2_v2/q2_summary.json"),
    ):
        result = validate_q2(scenario, segments, load_q2_solution(path))
        if not result.passed:
            raise SystemExit(f"{label} validator failed: {result.issues[:5]}")
    report = benchmark_saved_v2(ROOT)
    print("Q2 benchmark: v1 and v2 validator PASS")
    for row in report["comparison_rows"]:
        print(f"{row[0]}: J1={row[2]:.6f}, J2={row[4]:.3f} s, "
              f"J3={row[5]:.6f} kWh, J4={row[6]}")


if __name__ == "__main__":
    main()
