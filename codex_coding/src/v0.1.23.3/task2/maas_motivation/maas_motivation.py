"""MaaS-trace motivation for FluidPool (plan W6, docx/plans/2026-11-16_fluidpool_overview_and_next_plan.md).

Data: Alibaba cluster-trace-v2026-maas (ATC'26, "Model-as-a-Service in the Wild"), unpacked under
/home/zhujianian/clusterdata/cluster-trace-v2026-maas/data/. All reads are streamed: one parquet
file at a time, column projection, record batches -- the host also runs a live experiment, so the
process must stay small (target RSS < 3 GB).

Subcommands (each writes <out>/<name>.json; run in this order):
  daily   <data> <out>   stranded HBM inside allocations, cold-model residency, variant long tail,
                         host memory per instance; also the per-instance GPU-memory allocation of
                         the trace week (days 140-146) used by `weekly`
  split   <data> <out>   hash-partition instance_week_5min by instance_id into <out>/_week_parts/
                         (rows of one instance are spread over the shards)
  weekly  <data> <out>   5-minute volatility per instance (used HBM, host memory) and per cluster
                         (total stranded HBM)
  figures <out>          PNG figures from the JSON results

What the trace is: per-instance allocation and utilisation. There is NO node id and NO node
capacity, so free host memory per node and fragmentation across nodes cannot be measured from it.
"Stranded HBM" below = GPU memory allocated to an instance but not used by it
(gpu_mem_alloc * (1 - gpu_mem_util / 100)).
"""
import glob
import json
import math
import os
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

BATCH = 1_000_000
NPART = 32
WEEK_DAYS = range(140, 147)

# log-spaced histogram bins (GiB, GiB deltas, or fractions); zero values are counted apart.
# 801 bins over 8 decades: a quantile is reported as the geometric midpoint of its bin (2.3% wide),
# not as an edge -- with 321 bins the p90 of a quantum-spike distribution read as a bin edge.
EDGES = np.concatenate([[0.0], np.logspace(-3, 5, 801)])


class Hist:
    """Streaming histogram of a non-negative quantity, optionally weighted."""

    def __init__(self):
        self.c = np.zeros(len(EDGES) - 1)
        self.n = 0.0
        self.zero = 0.0
        self.total = 0.0

    def add(self, x, w=None):
        x = np.asarray(x, dtype=float)
        w = np.ones_like(x) if w is None else np.asarray(w, dtype=float)
        ok = np.isfinite(x) & np.isfinite(w) & (x >= 0)
        x, w = x[ok], w[ok]
        self.zero += w[x == 0].sum()
        self.n += w.sum()
        self.total += (x * w).sum()
        h, _ = np.histogram(np.clip(x, 0, EDGES[-1]), bins=EDGES, weights=w)
        self.c += h

    def pct(self, q):
        if self.n == 0:
            return None
        cum = np.cumsum(self.c)
        i = int(np.searchsorted(cum, q / 100.0 * self.n))
        lo, hi = EDGES[i], EDGES[i + 1]
        return float(math.sqrt(lo * hi) if lo > 0 else hi / 2)     # midpoint of the bin holding q

    def summary(self):
        return {"n": round(self.n, 1), "mean": round(self.total / self.n, 4) if self.n else None,
                "zero_share": round(self.zero / self.n, 4) if self.n else None,
                **{f"p{q}": self.pct(q) for q in (10, 25, 50, 75, 90, 99)}}

    def cdf(self):
        cum = np.cumsum(self.c) / max(self.n, 1e-12)
        cum = np.minimum(cum, 1.0)
        keep = np.r_[True, np.diff(cum) > 1e-6]
        return {"x": EDGES[1:][keep].round(5).tolist(), "F": cum[keep].round(5).tolist()}


def batches(path, cols):
    pf = pq.ParquetFile(path)
    for b in pf.iter_batches(batch_size=BATCH, columns=cols):
        yield b.to_pandas()


def dump(out, name, obj):
    os.makedirs(out, exist_ok=True)
    p = os.path.join(out, name + ".json")
    with open(p + ".tmp", "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(p + ".tmp", p)
    print("wrote", p, flush=True)


def rss_gib():
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS"):
                    return int(line.split()[1]) / 2 ** 20
    except OSError:
        return None


# --------------------------------------------------------------------------------------------
def cmd_daily(data, out):
    files = sorted(glob.glob(os.path.join(data, "instance_daily", "*.parquet")))
    assert files, "no instance_daily shards"
    cols = ["day", "instance_id", "model_id", "cluster_id", "workload", "gpu_count", "gpu_hours",
            "samples", "zero_samples", "gpu_mem_util", "mem_usage", "gpu_mem_alloc"]
    h_str_seg = Hist()          # stranded GiB per segment
    h_str_hw = Hist()           # stranded GiB weighted by segment hours (time-average view)
    h_frac_hw = Hist()          # stranded / allocated, weighted by allocated GiB-hours
    h_mem = Hist()              # host memory GiB per segment
    h_mem_hw = Hist()
    cd = {}                     # (cluster, day) -> [alloc GiB-h, stranded GiB-h, gpu-h]
    cold = {thr: {"gpu_h": 0.0, "alloc_GiBh": 0.0, "stranded_GiBh": 0.0, "segments": 0}
            for thr in (0.5, 0.9, 0.99)}
    tot = {"gpu_h": 0.0, "alloc_GiBh": 0.0, "stranded_GiBh": 0.0, "segments": 0, "rows": 0,
           "no_alloc_or_util": 0, "no_samples": 0}
    by_cls = {}                 # (class, workload) -> [gpu_h, cold90 gpu_h, alloc_GiBh, cold90 alloc_GiBh]
    cm = None                   # (cluster, model) -> gpu_h
    mod = None                  # model -> [gpu_h, samples, zero_samples]
    week_alloc = []             # per-instance GPU memory per GPU in the trace week
    for k, f in enumerate(files):
        for df in batches(f, cols):
            tot["rows"] += len(df)
            df = df[df["gpu_hours"].notna() & (df["gpu_hours"] > 0) & (df["gpu_count"] > 0)]
            hours = df["gpu_hours"] / df["gpu_count"]
            ok = df["gpu_mem_alloc"].notna() & df["gpu_mem_util"].notna() & (df["gpu_mem_alloc"] > 0)
            tot["no_alloc_or_util"] += int((~ok).sum())
            d = df[ok]
            hd = hours[ok]
            util = d["gpu_mem_util"].clip(0, 100) / 100.0
            stranded = d["gpu_mem_alloc"] * (1 - util)
            h_str_seg.add(stranded.values)
            h_str_hw.add(stranded.values, hd.values)
            h_frac_hw.add((1 - util).values, (d["gpu_mem_alloc"] * hd).values)
            g = pd.DataFrame({"c": d["cluster_id"].values, "d": d["day"].values,
                              "a": (d["gpu_mem_alloc"] * hd).values, "s": (stranded * hd).values,
                              "g": d["gpu_hours"].values}).groupby(["c", "d"]).sum()
            for (c, dd), r in g.iterrows():
                v = cd.setdefault((c, int(dd)), [0.0, 0.0, 0.0])
                v[0] += r["a"]
                v[1] += r["s"]
                v[2] += r["g"]
            tot["alloc_GiBh"] += float((d["gpu_mem_alloc"] * hd).sum())
            tot["stranded_GiBh"] += float((stranded * hd).sum())
            # cold residency (all rows with telemetry)
            ds = df[df["samples"].notna() & (df["samples"] > 0)]
            tot["no_samples"] += len(df) - len(ds)
            zf = ds["zero_samples"].fillna(0) / ds["samples"]
            hs = ds["gpu_hours"] / ds["gpu_count"]
            alloc_h = (ds["gpu_mem_alloc"].fillna(0) * hs)
            strd_h = (ds["gpu_mem_alloc"].fillna(0) * (1 - ds["gpu_mem_util"].fillna(100).clip(0, 100) / 100) * hs)
            tot["gpu_h"] += float(ds["gpu_hours"].sum())
            tot["segments"] += len(ds)
            for thr, v in cold.items():
                m = zf >= thr
                v["gpu_h"] += float(ds["gpu_hours"][m].sum())
                v["alloc_GiBh"] += float(alloc_h[m].sum())
                v["stranded_GiBh"] += float(strd_h[m].sum())
                v["segments"] += int(m.sum())
            cls = ds["model_id"].fillna("?").str.slice(0, 2)
            gg = pd.DataFrame({"cls": cls.values, "w": ds["workload"].fillna("?").values,
                               "g": ds["gpu_hours"].values, "gc": ds["gpu_hours"].where(zf >= 0.9, 0).values,
                               "a": alloc_h.values, "ac": alloc_h.where(zf >= 0.9, 0).values}).groupby(["cls", "w"]).sum()
            for key, r in gg.iterrows():
                v = by_cls.setdefault(key, [0.0, 0.0, 0.0, 0.0])
                v[0] += r["g"]
                v[1] += r["gc"]
                v[2] += r["a"]
                v[3] += r["ac"]
            # long tail
            s = ds.groupby(["cluster_id", "model_id"])["gpu_hours"].sum()
            cm = s if cm is None else cm.add(s, fill_value=0)
            ms = ds.groupby("model_id")[["gpu_hours", "samples", "zero_samples"]].sum()
            mod = ms if mod is None else mod.add(ms, fill_value=0)
            # host memory
            mm = df["mem_usage"].notna()
            h_mem.add(df["mem_usage"][mm].values)
            h_mem_hw.add(df["mem_usage"][mm].values, hours[mm].values)
            # trace-week allocation per GPU, for the weekly join
            w = d[d["day"].isin(WEEK_DAYS)]
            if len(w):
                week_alloc.append(pd.DataFrame({"instance_id": w["instance_id"].values,
                                                "alloc_per_gpu": (w["gpu_mem_alloc"] / w["gpu_count"]).values}))
        if week_alloc and sum(len(x) for x in week_alloc) > 2_000_000:
            week_alloc = [pd.concat(week_alloc).groupby("instance_id")["alloc_per_gpu"].median().reset_index()]
        print(f"daily {k + 1}/{len(files)} rows={tot['rows']} rss={rss_gib():.2f}GiB", flush=True)
    # (cluster, day) stranded: time-averaged GiB over the day
    cdf = pd.DataFrame([(c, d, *v) for (c, d), v in cd.items()], columns=["cluster", "day", "alloc_GiBh", "stranded_GiBh", "gpu_h"])
    cdf["stranded_avg_GiB"] = cdf["stranded_GiBh"] / 24
    cdf["alloc_avg_GiB"] = cdf["alloc_GiBh"] / 24
    cdf["stranded_frac"] = cdf["stranded_GiBh"] / cdf["alloc_GiBh"].where(cdf["alloc_GiBh"] > 0)
    per_cluster = cdf.groupby("cluster")[["stranded_avg_GiB", "alloc_avg_GiB"]].mean()
    # long tail per cluster
    cmd = cm.reset_index()
    cmd.columns = ["cluster", "model", "gpu_h"]
    tail = []
    for c, g in cmd.groupby("cluster"):
        v = np.sort(g["gpu_h"].values)[::-1]
        if v.sum() <= 0:
            continue
        top = max(1, math.ceil(0.1 * len(v)))
        tail.append({"cluster": c, "models": int(len(v)), "top10pct_share": float(v[:top].sum() / v.sum()),
                     "top1_share": float(v[0] / v.sum())})
    tail = pd.DataFrame(tail)
    mod = mod[mod["samples"] > 0]
    mod["zero_frac"] = mod["zero_samples"] / mod["samples"]
    gv = np.sort(mod["gpu_hours"].values)[::-1]
    top = max(1, math.ceil(0.1 * len(gv)))
    res = {
        "rows": tot["rows"], "segments_with_telemetry": tot["segments"],
        "rows_without_alloc_or_util": tot["no_alloc_or_util"], "rows_without_samples": tot["no_samples"],
        "stranded_hbm": {
            "per_segment_GiB": h_str_seg.summary(),
            "hour_weighted_GiB": h_str_hw.summary(),
            "unused_fraction_of_alloc_weighted": h_frac_hw.summary(),
            "cdf_unused_fraction_of_alloc_weighted": h_frac_hw.cdf(),
            "overall_unused_share_of_allocated_GiBh": round(tot["stranded_GiBh"] / tot["alloc_GiBh"], 4),
            "cluster_day_avg_stranded_GiB": {q: float(cdf["stranded_avg_GiB"].quantile(q / 100)) for q in (10, 50, 90, 99)},
            "cluster_day_stranded_frac": {q: float(cdf["stranded_frac"].quantile(q / 100)) for q in (10, 50, 90)},
            "per_cluster_mean_stranded_GiB": {q: float(per_cluster["stranded_avg_GiB"].quantile(q / 100)) for q in (10, 50, 90)},
            "all_clusters_mean_stranded_GiB": float(cdf.groupby("day")["stranded_avg_GiB"].sum().mean()),
            "all_clusters_mean_alloc_GiB": float(cdf.groupby("day")["alloc_avg_GiB"].sum().mean()),
            "cdf_hour_weighted": h_str_hw.cdf(),
        },
        "cold_residency": {
            "definition": "segment with zero_samples/samples >= threshold (zero duty cycle)",
            "total": {"gpu_h": tot["gpu_h"], "alloc_GiBh": tot["alloc_GiBh"], "stranded_GiBh": tot["stranded_GiBh"]},
            **{f"zero_ge_{thr}": {**v, "gpu_h_share": round(v["gpu_h"] / tot["gpu_h"], 4),
                                  "alloc_share": round(v["alloc_GiBh"] / tot["alloc_GiBh"], 4) if tot["alloc_GiBh"] else None,
                                  "avg_alloc_GiB_held": round(v["alloc_GiBh"] / (182 * 24), 1)}
               for thr, v in cold.items()},
            "by_class_workload": [{"model_class": c, "workload": w, "gpu_h": v[0],
                                   "cold90_gpu_h_share": round(v[1] / v[0], 4) if v[0] else None,
                                   "cold90_alloc_share": round(v[3] / v[2], 4) if v[2] else None}
                                  for (c, w), v in sorted(by_cls.items())],
        },
        "variant_long_tail": {
            "models_total": int(len(mod)),
            "models_per_cluster": {q: float(tail["models"].quantile(q / 100)) for q in (10, 50, 90, 100)},
            "top10pct_share_of_gpu_h_per_cluster": {q: float(tail["top10pct_share"].quantile(q / 100)) for q in (10, 50, 90)},
            "top10pct_share_global": float(gv[:top].sum() / gv.sum()),
            "models_cold_ge_0.9": int((mod["zero_frac"] >= 0.9).sum()),
            "models_cold_ge_0.5": int((mod["zero_frac"] >= 0.5).sum()),
            "gpu_h_share_of_models_cold_ge_0.9": float(mod.loc[mod["zero_frac"] >= 0.9, "gpu_hours"].sum() / mod["gpu_hours"].sum()),
        },
        "host_memory": {
            "note": "per-instance host memory use only; node-level free host memory is NOT in the trace",
            "per_segment_GiB": h_mem.summary(), "hour_weighted_GiB": h_mem_hw.summary(),
        },
    }
    dump(out, "daily", res)
    cdf.to_csv(os.path.join(out, "cluster_day.csv"), index=False)
    wa = pd.concat(week_alloc).groupby("instance_id")["alloc_per_gpu"].median()
    wa.reset_index().to_parquet(os.path.join(out, "_week_alloc.parquet"))
    print("week instances with alloc:", len(wa), flush=True)


# --------------------------------------------------------------------------------------------
def cmd_split(data, out):
    files = sorted(glob.glob(os.path.join(data, "instance_week_5min", "*.parquet")))
    assert files, "no instance_week_5min shards"
    pd_dir = os.path.join(out, "_week_parts")
    os.makedirs(pd_dir, exist_ok=True)
    cols = ["instance_id", "time_s", "cluster_id", "gpu_count", "gpu_mem_util", "mem_usage"]
    n = 0
    for k, f in enumerate(files):
        for j, df in enumerate(batches(f, cols)):
            part = (pd.util.hash_pandas_object(df["instance_id"], index=False).values % NPART).astype(int)
            for p in range(NPART):
                sub = df[part == p]
                if len(sub):
                    sub.to_parquet(os.path.join(pd_dir, f"p{p:02d}_f{k:02d}_b{j:03d}.parquet"), index=False)
            n += len(df)
        print(f"split {k + 1}/{len(files)} rows={n} rss={rss_gib():.2f}GiB", flush=True)


def cmd_weekly(data, out):
    pd_dir = os.path.join(out, "_week_parts")
    wa = pd.read_parquet(os.path.join(out, "_week_alloc.parquet")).set_index("instance_id")["alloc_per_gpu"]
    h_used = Hist()             # |delta used HBM| GiB between consecutive 5-min windows of an instance
    h_used_rel = Hist()         # |delta| / allocated
    h_mem = Hist()              # |delta host memory| GiB
    h_mem_rel = Hist()
    cl = None                   # (cluster, time_s) -> stranded GiB, alloc GiB
    stats = {"rows": 0, "rows_no_alloc": 0, "instances": 0, "pairs": 0}
    for p in range(NPART):
        fs = sorted(glob.glob(os.path.join(pd_dir, f"p{p:02d}_*.parquet")))
        df = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
        stats["rows"] += len(df)
        df["apg"] = df["instance_id"].map(wa)
        stats["rows_no_alloc"] += int(df["apg"].isna().sum())
        df["alloc"] = df["apg"] * df["gpu_count"]
        df["used"] = df["alloc"] * df["gpu_mem_util"].clip(0, 100) / 100
        df["stranded"] = df["alloc"] - df["used"]
        df = df.sort_values(["instance_id", "time_s"])
        same = (df["instance_id"].values[1:] == df["instance_id"].values[:-1]) & \
               (np.diff(df["time_s"].values) == 300)
        du = np.abs(np.diff(df["used"].values))[same]
        al = df["alloc"].values[1:][same]
        okk = np.isfinite(du)
        h_used.add(du[okk])
        h_used_rel.add((du / al)[okk & (al > 0)])
        dm = np.abs(np.diff(df["mem_usage"].values))[same]
        mm = df["mem_usage"].values[1:][same]
        okm = np.isfinite(dm)
        h_mem.add(dm[okm])
        h_mem_rel.add((dm / np.where(mm > 0, mm, np.nan))[okm & (mm > 0)])
        stats["pairs"] += int(same.sum())
        stats["instances"] += int(df["instance_id"].nunique())
        g = df.groupby(["cluster_id", "time_s"])[["stranded", "alloc"]].sum(min_count=1)
        cl = g if cl is None else cl.add(g, fill_value=0)
        del df
        print(f"weekly part {p + 1}/{NPART} rows={stats['rows']} rss={rss_gib():.2f}GiB", flush=True)
    # per-cluster change of total stranded HBM
    cl = cl.reset_index().sort_values(["cluster_id", "time_s"])
    h_cl5 = Hist()
    h_cl5_rel = Hist()
    h_cl60 = Hist()
    h_cl60_rel = Hist()
    swing = []
    for c, g in cl.groupby("cluster_id"):
        g = g.set_index("time_s")["stranded"].dropna()
        if len(g) < 13:
            continue
        mean = g.mean()
        d5 = g.diff().dropna()
        d5 = d5[np.diff(g.index.values) == 300]
        h_cl5.add(np.abs(d5.values))
        if mean > 0:
            h_cl5_rel.add(np.abs(d5.values) / mean)
        g60 = g.reindex(range(int(g.index.min()), int(g.index.max()) + 1, 300))
        d60 = (g60.shift(-12) - g60).dropna()
        h_cl60.add(np.abs(d60.values))
        if mean > 0:
            h_cl60_rel.add(np.abs(d60.values) / mean)
            swing.append({"cluster": c, "mean_stranded_GiB": float(mean),
                          "p5_GiB": float(g.quantile(0.05)), "p95_GiB": float(g.quantile(0.95))})
    sw = pd.DataFrame(swing)
    res = {
        **stats,
        "note": "used HBM = alloc_per_gpu(daily, days 140-146, median) * gpu_count * gpu_mem_util; "
                "instances without a daily allocation are skipped",
        "instance_used_hbm_abs_delta_5min_GiB": h_used.summary(),
        "instance_used_hbm_rel_delta_5min": h_used_rel.summary(),
        "instance_host_mem_abs_delta_5min_GiB": h_mem.summary(),
        "instance_host_mem_rel_delta_5min": h_mem_rel.summary(),
        "cluster_stranded_abs_delta_5min_GiB": h_cl5.summary(),
        "cluster_stranded_rel_delta_5min": h_cl5_rel.summary(),
        "cluster_stranded_abs_delta_1h_GiB": h_cl60.summary(),
        "cluster_stranded_rel_delta_1h": h_cl60_rel.summary(),
        "cluster_stranded_p95_over_p5": {q: float((sw["p95_GiB"] / sw["p5_GiB"].where(sw["p5_GiB"] > 0)).quantile(q / 100))
                                         for q in (10, 50, 90)} if len(sw) else None,
        "cdf_cluster_rel_delta_1h": h_cl60_rel.cdf(),
        "cdf_cluster_rel_delta_5min": h_cl5_rel.cdf(),
        "cdf_instance_rel_delta_5min": h_used_rel.cdf(),
    }
    dump(out, "weekly", res)
    cl.to_csv(os.path.join(out, "cluster_5min_stranded.csv"), index=False)


# --------------------------------------------------------------------------------------------
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
S1, S2, S3 = "#2a78d6", "#eb6834", "#1baf7a"     # reference categorical slots 1-3 (validated all-pairs)


def _ax(ax, title, xlabel, ylabel):
    ax.set_facecolor(SURF)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.set_title(title, color=INK, fontsize=11, loc="left")
    ax.set_xlabel(xlabel, color=INK2, fontsize=9)
    ax.set_ylabel(ylabel, color=INK2, fontsize=9)


def cmd_figures(out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    daily = json.load(open(os.path.join(out, "daily.json")))
    weekly = json.load(open(os.path.join(out, "weekly.json"))) if os.path.exists(os.path.join(out, "weekly.json")) else None
    # Fig 1, two panels, because one axis was answering two questions at once. A GiB per segment
    # says how big the instance is (the trace's allocations are quanta: 45 / 95.6 / 192 GiB per GPU),
    # not how wasteful it is; hour-weighting a level also mixes "how long" into "how much".
    #   (a) rate: share of an instance's own allocation that holds no tensors, weighted by allocated
    #       memory-hours -- scale-free, the quantity a keep-alive system can reclaim;
    #   (b) capacity: a cluster's average stranded GiB over a day (concurrent sum, so no
    #       double counting), with model footprint references for scale.
    sh = daily["stranded_hbm"]
    fig, axs = plt.subplots(1, 2, figsize=(9.6, 3.4), facecolor=SURF)
    c = sh["cdf_unused_fraction_of_alloc_weighted"]
    ax = axs[0]
    ax.plot(np.array(c["x"]) * 100, c["F"], color=S1, linewidth=2)
    for q, y in ((50, 0.5), (90, 0.9)):
        v = next((x * 100 for x, f in zip(c["x"], c["F"]) if f >= y), None)
        if v:
            ax.annotate(f"p{q}: {v:.0f}%", (v, y), fontsize=8, color=INK2, xytext=(4, -10), textcoords="offset points")
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 1)
    _ax(ax, "(a) Unused share of each instance's allocation", "unused share (%)",
        "share of allocated memory-hours")
    ax = axs[1]
    v = np.sort(pd.read_csv(os.path.join(out, "cluster_day.csv"))["stranded_avg_GiB"].dropna().values)
    v = v[v > 0]
    bins = np.logspace(0, math.ceil(math.log10(max(v.max(), 10))), 49)
    ax.hist(v, bins=bins, color=S2, rwidth=0.92)
    ax.set_xscale("log")
    for g, yf, ha in ((67, 0.97, "right"), (344, 0.88, "left")):   # one 35B model / one GLM-class model
        ax.axvline(g, color=INK2, linewidth=1, linestyle=(0, (3, 3)))
        ax.annotate(f"one {g} GiB model", (g, ax.get_ylim()[1] * yf), fontsize=7, color=INK2,
                    ha=ha, va="top", xytext=(-3 if ha == "right" else 3, 0), textcoords="offset points")
    _ax(ax, "(b) Stranded memory per cluster (day average)",
        "unused but allocated GPU memory (GiB)", "cluster-days")
    fig.tight_layout()
    fig.savefig(os.path.join(out, "fig_stranded_hbm_cdf.png"), dpi=160, facecolor=SURF)
    plt.close(fig)
    # Fig 2: cold residency share by threshold (GPU-hours and allocated memory)
    cr = daily["cold_residency"]
    thr = ["0.5", "0.9", "0.99"]
    g = [cr[f"zero_ge_{t}"]["gpu_h_share"] * 100 for t in thr]
    a = [cr[f"zero_ge_{t}"]["alloc_share"] * 100 for t in thr]
    fig, ax = plt.subplots(figsize=(5.2, 3.4), facecolor=SURF)
    x = np.arange(len(thr))
    b1 = ax.bar(x - 0.19, g, 0.36, color=S1, label="GPU-hours")
    b2 = ax.bar(x + 0.19, a, 0.36, color=S2, label="Allocated GPU memory")
    for bars in (b1, b2):
        for r in bars:
            ax.annotate(f"{r.get_height():.1f}%", (r.get_x() + r.get_width() / 2, r.get_height()),
                        ha="center", va="bottom", fontsize=8, color=INK2, xytext=(0, 2), textcoords="offset points")
    ax.set_xticks(x, [f"≥{int(float(t) * 100)}%" for t in thr])
    _ax(ax, "Held by instances that are (almost) never busy", "share of telemetry samples with zero duty cycle",
        "share of total (%)")
    ax.legend(frameon=False, fontsize=8, labelcolor=INK2)
    fig.tight_layout()
    fig.savefig(os.path.join(out, "fig_cold_residency.png"), dpi=160, facecolor=SURF)
    plt.close(fig)
    if weekly:
        # Fig 3: how fast a cluster's total unused GPU memory changes -- same quantity, two horizons
        # (per-cluster |delta| as % of that cluster's mean unused memory). A per-instance line was
        # flat here (85% of instance 5-min deltas are zero), so it compared different quantities.
        c5 = weekly["cdf_cluster_rel_delta_5min"]
        c60 = weekly["cdf_cluster_rel_delta_1h"]
        fig, ax = plt.subplots(figsize=(5.2, 3.4), facecolor=SURF)
        ax.plot(np.array(c60["x"]) * 100, c60["F"], color=S1, linewidth=2, label="1 h apart")
        ax.plot(np.array(c5["x"]) * 100, c5["F"], color=S2, linewidth=2, label="5 min apart")
        for q, y in ((50, 0.5), (90, 0.9)):
            for h, lo in ((c5, "5 min"), (c60, "1 h")):
                v = next((x * 100 for x, f in zip(h["x"], h["F"]) if f >= y), None)
                if v:
                    ax.annotate(f"{lo} p{q}: {v:.0f}%", (v, y), fontsize=8, color=INK2,
                                xytext=(4, -10 if lo == "5 min" else 4), textcoords="offset points")
        ax.set_xscale("log")
        ax.set_xlim(0.1, 300)
        _ax(ax, "Change of a cluster's unused GPU memory", "|change| over the interval, % of the cluster's mean",
            "CDF over cluster x interval")
        ax.legend(frameon=False, fontsize=8, labelcolor=INK2, loc="lower right")
        fig.tight_layout()
        fig.savefig(os.path.join(out, "fig_stranded_volatility.png"), dpi=160, facecolor=SURF)
        plt.close(fig)
    print("figures written", flush=True)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "figures":
        cmd_figures(sys.argv[2])
    else:
        {"daily": cmd_daily, "split": cmd_split, "weekly": cmd_weekly}[cmd](sys.argv[2], sys.argv[3])
