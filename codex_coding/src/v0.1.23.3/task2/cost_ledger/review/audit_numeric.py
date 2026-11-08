"""Small independent exact-arithmetic reconstruction of the declared fit gates."""
from fractions import Fraction
from statistics import median

from audit_core import CALIBRATION_ORDER, CONFIGS, expected_chunks


def solve(matrix, vector):
    rows = [[Fraction(x) for x in row] + [Fraction(y)] for row, y in zip(matrix, vector)]
    size = len(rows)
    for col in range(size):
        pivot = next((i for i in range(col, size) if rows[i][col]), None)
        if pivot is None:
            raise ValueError("rank deficient")
        rows[col], rows[pivot] = rows[pivot], rows[col]
        factor = rows[col][col]
        rows[col] = [x/factor for x in rows[col]]
        for index in range(size):
            if index != col:
                factor = rows[index][col]
                rows[index] = [x-factor*y for x, y in zip(rows[index], rows[col])]
    return [row[-1] for row in rows]


def fit(op_sizes, observations, columns):
    all_features = [[1, sum(sizes), len(sizes), len(expected_chunks(sizes))] for sizes in op_sizes]
    features = [[row[i] for i in columns] for row in all_features]
    gram = [[sum(row[i]*row[j] for row in features) for j in range(len(columns))]
            for i in range(len(columns))]
    target = [sum(row[i]*value for row, value in zip(features, observations))
              for i in range(len(columns))]
    coefficients = solve(gram, target)
    residuals = [Fraction(value)-sum(c*x for c, x in zip(coefficients, row))
                 for row, value in zip(features, observations)]
    return coefficients, residuals


def reconstruct(samples, observer):
    by_id = {row.get("case_id", row.get("config_id")): row for row in samples}
    if list(by_id) != CALIBRATION_ORDER:
        raise ValueError("calibration order must contain exactly the six preregistered requests")
    result = {}
    for boundary in ("service_ns", "worker_ns"):
        times = {name: Fraction(row["measured"][boundary]) for name, row in by_id.items()}
        anchor_delta = abs(times["L3_repeat"]-times["L3"])
        scale = max(Fraction(5000), Fraction(observer["clock_extra_p95_ns"]),
                    Fraction(observer["dummy_log_scale_ns"]), anchor_delta)
        drift_limit = max(Fraction(20000), (times["L3_repeat"]+times["L3"])/20)
        c = times["L3"]-times["L2"]
        d = times["L4"]-times["L3"]
        b32 = times["L2"]-times["L1"]-d
        fifth = abs(times["L5"]-2*times["L2"]+times["L1"])
        candidates = {}
        ids = CALIBRATION_ORDER[:-1]
        for name, columns in (("a_bN", (0, 1)), ("a_bN_cK", (0, 1, 2)),
                              ("a_bN_cK_dC", (0, 1, 2, 3))):
            coeffs, residuals = fit([CONFIGS[x] for x in ids], [times[x] for x in ids], columns)
            shape_pass = (c > 2*scale and d > 2*scale and b32 > 4*scale and fifth <= 4*scale)
            admissible = (all(x >= 0 for x in coeffs) and max(map(abs, residuals)) <= 2*scale
                          and (len(columns) < 4 or shape_pass))
            predictions = {}
            for holdout in ("V2", "V1"):
                sizes = CONFIGS[holdout]
                values = [1, sum(sizes), len(sizes), len(expected_chunks(sizes))]
                predictions[holdout] = sum(c*values[i] for i, c in zip(columns, coeffs))
            candidates[name] = {"raw_coefficients": coeffs, "raw_residuals": residuals,
                                "admissible": admissible,
                                "admitted": anchor_delta <= drift_limit and admissible,
                                "predictions": predictions}
        result[boundary] = {"E_ns": scale, "anchor_delta_ns": anchor_delta,
                            "drift_limit_ns": drift_limit, "drift_passed": anchor_delta <= drift_limit,
                            "direct_c_ns": c, "direct_d_ns": d, "direct_32b_ns": b32,
                            "fifth_difference_abs_ns": fifth, "candidates": candidates}
    training = [by_id[x] for x in CALIBRATION_ORDER[:-1]]
    result["controller_residual_ns"] = median(Fraction(row["measured"]["controller_execute_ns"])
        -Fraction(row["measured"]["worker_ns"]) for row in training)
    result["filled_residual_ns"] = median(Fraction(row["measured"]["full_filled_ns"])
        -Fraction(row["measured"]["controller_execute_ns"]) for row in training)
    return result


def serializable(value):
    if isinstance(value, Fraction):
        return {"numerator": value.numerator, "denominator": value.denominator,
                "float_ns": float(value)}
    if isinstance(value, dict):
        return {k: serializable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [serializable(v) for v in value]
    return value
