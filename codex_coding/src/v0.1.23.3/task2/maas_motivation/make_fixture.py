"""Synthetic MaaS-shaped shards for a smoke test of maas_motivation.py (no real data needed).
  make_fixture.py <dir>      writes <dir>/instance_daily/*.parquet and <dir>/instance_week_5min/*.parquet"""
import os
import sys

import numpy as np
import pandas as pd

d = sys.argv[1]
rng = np.random.default_rng(0)
os.makedirs(f"{d}/instance_daily", exist_ok=True)
os.makedirs(f"{d}/instance_week_5min", exist_ok=True)
N = 4000
inst = [f"i{k}" for k in range(N)]
for s in range(3):
    rows = 20000
    k = rng.integers(0, N, rows)
    gc = rng.choice([1.0, 2.0, 4.0, 8.0, 0.5], rows)
    samples = rng.integers(1, 4000, rows)
    df = pd.DataFrame({
        "day": rng.integers(130, 150, rows), "instance_id": [inst[i] for i in k],
        "app_id": "a", "model_id": [rng.choice(["b_", "s_", "x_", "o_"]) + str(i % 300) for i in k],
        "base_id": "-", "cluster_id": [f"c{i % 7}" for i in k], "site_id": "s", "gpu_type": "g",
        "workload": rng.choice(["online", "offline"], rows), "model_arch": "dense", "params_b": 7.0,
        "pd_role": "fused", "gpu_count": gc, "gpu_hours": gc * rng.uniform(0.1, 24, rows),
        "samples": samples, "zero_samples": (samples * rng.beta(0.5, 0.5, rows)).astype(int),
        "sm_util": 10.0, "gpu_mem_util": np.where(rng.random(rows) < 0.05, np.nan, rng.uniform(0, 100, rows)),
        "cpu_usage": 1.0, "mem_usage": rng.uniform(1, 200, rows), "base_arch": "dense", "variant_arch": "dense",
        "gpu_mem_alloc": gc * 64.0})
    df.to_parquet(f"{d}/instance_daily/part_{s}.parquet", index=False)
t0 = 140 * 86400
for s in range(2):
    ids = inst[s::2][:500]
    ts = np.arange(t0, t0 + 300 * 200, 300)
    df = pd.DataFrame([(i, t) for i in ids for t in ts], columns=["instance_id", "time_s"])
    df["cluster_id"] = [f"c{int(i[1:]) % 7}" for i in df["instance_id"]]
    df["gpu_count"] = 2.0
    df["sm_util"] = 5.0
    df["gpu_mem_util"] = rng.uniform(20, 90, len(df))
    df["cpu_usage"] = 1.0
    df["mem_usage"] = rng.uniform(5, 50, len(df))
    df["samples"] = 15
    df.sample(frac=1, random_state=s).to_parquet(f"{d}/instance_week_5min/part_{s}.parquet", index=False)
print("fixture written", d)
