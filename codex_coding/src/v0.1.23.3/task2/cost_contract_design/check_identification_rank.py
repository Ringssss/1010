"""Check proposed local calibration identifiability using exact CPU arithmetic."""
import argparse
import json
from fractions import Fraction
from pathlib import Path


CASES = [
    ("T1", [32]),
    ("T2", [64]),
    ("T3", [32, 32]),
    ("T4", [48, 16]),
    ("T5", [96]),
    ("H1", [16, 16, 16]),
    ("H2", [40, 24, 16]),
]


def row(parts):
    return [1, sum(parts), len(parts), sum((n + 31) // 32 for n in parts)]


def rank(matrix):
    work = [[Fraction(v) for v in line] for line in matrix]
    pivot = 0
    for col in range(len(work[0])):
        found = next((i for i in range(pivot, len(work)) if work[i][col]), None)
        if found is None:
            continue
        work[pivot], work[found] = work[found], work[pivot]
        value = work[pivot][col]
        work[pivot] = [v / value for v in work[pivot]]
        for i in range(len(work)):
            if i != pivot:
                value = work[i][col]
                work[i] = [v - value * p for v, p in zip(work[i], work[pivot])]
        pivot += 1
    return pivot


def determinant(matrix):
    if len(matrix) == 1:
        return Fraction(matrix[0][0])
    return sum(Fraction((-1) ** col * matrix[0][col]) * determinant(
        [line[:col] + line[col + 1:] for line in matrix[1:]])
        for col in range(len(matrix)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    cases = []
    for name, parts in CASES:
        chunks = []
        for n in parts:
            chunks.append([min(32, n - pos) for pos in range(0, n, 32)])
        cases.append(dict(id=name, operation_mib=parts, chunk_mib=chunks,
                          columns=row(parts)))
    training = [case["columns"] for case in cases[:5]]
    # Below-threshold historical-style measurements make K and C identical.
    confounded = [[1, 4, 1, 1], [1, 16, 1, 1], [1, 4, 4, 4]]
    synthetic_coefficients = [Fraction(100), Fraction(2), Fraction(10), Fraction(3)]
    synthetic_us = {case["id"]: sum(a * b for a, b in zip(
        case["columns"], synthetic_coefficients)) for case in cases}
    gamma = synthetic_us["T4"] - synthetic_us["T3"]
    operation = synthetic_us["T3"] - synthetic_us["T2"]
    beta = (synthetic_us["T2"] - synthetic_us["T1"] - gamma) / 32
    intercept = synthetic_us["T1"] - 32 * beta - operation - gamma
    recovered = [intercept, beta, operation, gamma]
    checks = dict(
        primary_rank_four=rank(training) == 4,
        primary_minimal_minor_nonzero=determinant(training[:4]) != 0,
        below_chunk_K_C_confounded=rank(confounded) == 3,
        confounded_null_direction_all_zero=all(sum(a * b for a, b in zip(
            line, [0, 0, 1, -1])) == 0 for line in confounded),
        fifth_point_affine_consistency=synthetic_us["T5"] - 2 * synthetic_us["T2"] + synthetic_us["T1"] == 0,
        synthetic_coefficients_recovered=recovered == synthetic_coefficients,
        holdout_unseen_N_and_K=all(case["columns"][1] not in {line[1] for line in training}
            and case["columns"][2] not in {line[2] for line in training} for case in cases[5:]),
        holdout_K_extrapolates=all(case["columns"][2] > max(line[2] for line in training)
            for case in cases[5:]),
    )
    sequence = ["T3", "T1", "T4", "T2", "T5", "T3", "H2", "H1"]
    by_id = {case["id"]: case for case in cases}
    totals = [sum(by_id[name]["columns"][col] for name in sequence) for col in (1, 2, 3)]
    output = dict(status="cpu_design_rank_checks_passed" if all(checks.values()) else "failed",
                  hardware_measurements=False, chunk_mib=32,
                  columns=["conditional_request_intercept", "total_MiB", "operations", "actual_chunk_sync_count"],
                  cases=cases, training_rank=rank(training), minimal_minor_determinant=str(determinant(training[:4])),
                  confounded_rank=rank(confounded), checks=checks,
                  synthetic_units="microseconds; illustrative algebra only, not measured costs",
                  synthetic_coefficients=[str(v) for v in synthetic_coefficients],
                  synthetic_costs={key: str(value) for key, value in synthetic_us.items()},
                  proposed_sequence=sequence, requests=len(sequence),
                  payload_MiB=totals[0], operation_count=totals[1], chunk_sync_count=totals[2],
                  caveat="Full rank is necessary, not sufficient: hidden state and size-dependent chunk costs remain untested.")
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(dict(status=output["status"], checks=len(checks), output=str(path)), ensure_ascii=False))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
