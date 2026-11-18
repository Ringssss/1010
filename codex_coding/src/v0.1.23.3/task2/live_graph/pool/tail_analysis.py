"""Tail analysis of a formal maas3 run: what drives the slow misses.
  python3 -I tail_analysis.py <run_dir>   -> <run_dir>/tail_analysis.json, summary on stdout

Per system, over measured misses (event=request, phase!=warmup, kind!=ipc_hit), each joined to
the restore records of its wave (a request record follows its restores in run.log; GLM is two
restores, glm53-n28 and glm53-n30):

1. cold bytes: file + lssd + rstage (local NVMe, durable file, a peer's NVMe/file via staging) over
   all bytes of the activation, and the time those bytes explain. slow_s(restore) = max over slow
   classes of bytes / that class's observed rate (the executor's per-class rate, GB/s); explained
   = min(slow_s, total_s). A miss is "slow-bound" when slow_s >= 0.5 x its restore time.
2. outlier: one request index in full detail (bursts active, per-path bytes/rates, prof, plan).
3. GLM misses: slow bytes planned vs stolen, preceding coverage losses (evict lost_fast_copy,
   demote left_slow_GiB) since the previous GLM activation, and whether GLM was in the state's
   cold list at its wave.
4. relay: bytes via relay:* per miss and the latency delta vs the same request index in fluidpool.
Stdlib only."""
import collections
import json
import os
import sys

GB = 1e9
GiB = 1 << 30
SLOW = ("file", "lssd", "rstage")
d = sys.argv[1]
SYS = [s for s in ("fluidpool", "fluidpool_relay", "janus", "sglang") if os.path.exists(os.path.join(d, s, "run.log"))]
OUTLIER = int(os.environ.get("OUTLIER_I", "110"))


def load(s):
    rows = []
    for line in open(os.path.join(d, s, "run.log")):
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    st = [k for k, r in enumerate(rows) if r.get("event") == "run_start"]
    return rows[st[-1]:] if st else rows


def cls_group(k):
    """Executor class -> family."""
    if k.startswith("net:"):
        return "net"
    if k.startswith("relay:"):
        return "relay"
    if k.startswith("rstage:"):
        return "rstage"
    return k


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def restore_view(r):
    paths = r.get("paths") or {}
    rates = r.get("rates") or {}
    by = collections.Counter()
    slow_s = 0.0
    slow_planned, slow_stolen = 0, 0
    for k, v in paths.items():
        b = v["bytes"] if isinstance(v, dict) else v
        g = cls_group(k)
        by[g] += b
        if g in SLOW:
            rate = rates.get(k) or rates.get(g)
            if rate:
                slow_s = max(slow_s, b / GB / rate)
            ch, st = v.get("chunks", 0), v.get("stolen", 0)
            frac = (st / ch) if ch else 0.0
            slow_stolen += b * frac
            slow_planned += b * (1 - frac)
    tot = sum(by.values())
    return {"model": r["model"], "node": r["node"], "cards": r.get("cards"), "total_s": r["total_s"],
            "predicted_s": r.get("predicted_s"), "bytes": tot, "by": dict(by), "slow_bytes": sum(by[g] for g in SLOW),
            "slow_s": slow_s, "slow_planned": slow_planned, "slow_stolen": slow_stolen,
            "relay": r.get("relay") or {}, "rates": rates, "prof": r.get("prof") or {},
            "paths": {k: (v["bytes"] if isinstance(v, dict) else v) for k, v in paths.items()},
            "plan_cap": {k: (r.get("plan") or {}).get(k) for k in ("cap_port", "cap_card", "cap_slow", "src_tx_share", "relay")}}


def analyze(s):
    rows = load(s)
    misses, pending = [], []
    state, last_state = None, None
    losses = collections.Counter()           # model -> chunks/GiB lost since its last activation
    left_slow = collections.Counter()
    for r in rows:
        e = r.get("event")
        if e == "restore":
            pending.append(r)
        elif e == "state":
            last_state = r
        elif e == "evict":
            for m, k in (r.get("lost_fast_copy") or {}).items():
                losses[m] += k
        elif e == "demote":
            left_slow[r.get("model")] += r.get("left_slow_GiB") or 0.0
        elif e == "request":
            if r.get("phase") == "warmup" or r.get("kind") == "ipc_hit":
                continue
            what = r["what"]
            if what == "glm53":
                want = [("glm53-n28", "node28"), ("glm53-n30", "node30")]
            else:
                m, n = what.split("@")
                want = [(m, n)]
            got = []
            for m, n in want:
                hit = next((x for x in reversed(pending) if x["model"] == m and x["node"] == n), None)
                if hit is not None:
                    got.append(hit)
                    pending.remove(hit)
            views = [restore_view(x) for x in got]
            tot = sum(v["bytes"] for v in views)
            slow = sum(v["slow_bytes"] for v in views)
            slow_s = max([v["slow_s"] for v in views] or [0.0])
            lat = r["latency_s"]
            rec = {"i": r["i"], "what": what, "phase": r["phase"], "kind": r["kind"], "latency_s": lat,
                   "GiB": r.get("GiB"), "bursts_on_node": r.get("burst_kinds_on_node") or [],
                   "bursts": r.get("bursts") or [], "during": r.get("during_bursts") or [],
                   "cold_frac": (slow / tot) if tot else None, "slow_GiB": slow / GiB,
                   "slow_s": slow_s, "slow_explained_s": min(slow_s, lat),
                   "slow_bound": bool(tot) and slow_s >= 0.5 * max(v["total_s"] for v in views),
                   "relay_GiB": sum(v["by"].get("relay", 0) for v in views) / GiB,
                   "relay_cards": [v["relay"] for v in views if v["relay"]],
                   "restores": views,
                   "state_cold": (last_state or {}).get("cold"), "state_fast_coverage": (last_state or {}).get("fast_coverage")}
            mods = [m for m, _ in want]
            rec["lost_chunks_since_last"] = {m: losses.pop(m, 0) for m in mods}
            rec["demote_left_slow_GiB_since_last"] = {m: round(left_slow.pop(m, 0.0), 2) for m in mods}
            misses.append(rec)
    return misses


def summarize(s, ms):
    lat = [m["latency_s"] for m in ms]
    tot_t = sum(lat)
    expl = sum(m["slow_explained_s"] for m in ms)
    p95, p99 = pct(lat, 0.95), pct(lat, 0.99)
    tail = [m for m in ms if m["latency_s"] >= p95]
    burst = [m for m in ms if m["phase"] == "burst"]
    bl = [m["latency_s"] for m in burst]
    bp95 = pct(bl, 0.95)
    btail = [m for m in burst if m["latency_s"] >= bp95]
    cf = [m["cold_frac"] for m in ms if m["cold_frac"] is not None]
    return {
        "misses": len(ms), "mean_s": round(sum(lat) / len(lat), 3) if lat else None, "p95_s": p95, "p99_s": p99,
        "cold_frac": {"mean": round(sum(cf) / len(cf), 4) if cf else None, "p50": pct(cf, .5), "p90": pct(cf, .9),
                      "max": max(cf) if cf else None, "zero_share": round(sum(1 for x in cf if x == 0) / len(cf), 3) if cf else None},
        "slow_GiB_total": round(sum(m["slow_GiB"] for m in ms), 1),
        "share_of_miss_time_explained_by_slow_bytes": round(expl / tot_t, 3) if tot_t else None,
        "slow_bound_misses": sum(m["slow_bound"] for m in ms),
        "tail_ge_p95": [{"i": m["i"], "what": m["what"], "s": m["latency_s"], "cold_frac": round(m["cold_frac"] or 0, 3),
                         "slow_s": round(m["slow_s"], 2), "slow_bound": m["slow_bound"]} for m in sorted(tail, key=lambda x: -x["latency_s"])],
        "burst_p95_s": bp95,
        "burst_tail_slow_bound_share": round(sum(m["slow_bound"] for m in btail) / len(btail), 3) if btail else None,
        "burst_tail_time_explained_by_slow": round(sum(m["slow_explained_s"] for m in btail) / sum(m["latency_s"] for m in btail), 3) if btail else None,
    }


out = {"dir": d, "systems": {}}
all_ms = {}
for s in SYS:
    ms = analyze(s)
    all_ms[s] = ms
    out["systems"][s] = summarize(s, ms)

# 2. outlier
fp = {m["i"]: m for m in all_ms.get("fluidpool", [])}
rp = {m["i"]: m for m in all_ms.get("fluidpool_relay", [])}
sched = json.load(open(os.path.join(d, "burst_schedule.json")))
evs = sched["events"] if isinstance(sched.get("events"), list) else []
o = fp.get(OUTLIER)
if o:
    act = [e for e in evs if e["start_req"] <= OUTLIER < e["end_req"] or (e["start_req"] == OUTLIER)]
    out["outlier"] = {"i": OUTLIER, "fluidpool": {k: o[k] for k in ("what", "latency_s", "cold_frac", "slow_GiB", "slow_s", "bursts", "bursts_on_node", "during", "relay_GiB")},
                      "restores": [{k: v[k] for k in ("model", "node", "cards", "total_s", "predicted_s", "by", "paths", "rates", "prof", "plan_cap")} for v in o["restores"]],
                      "bursts_active": act,
                      "fluidpool_relay_same_i": ({k: rp[OUTLIER][k] for k in ("what", "latency_s", "cold_frac", "relay_GiB", "bursts_on_node")} if OUTLIER in rp else None)}

# 3. GLM
glm = {}
for s in ("fluidpool", "fluidpool_relay"):
    glm[s] = [{"i": m["i"], "latency_s": m["latency_s"], "slow_GiB": round(m["slow_GiB"], 1),
               "slow_planned_GiB": round(sum(v["slow_planned"] for v in m["restores"]) / GiB, 1),
               "slow_stolen_GiB": round(sum(v["slow_stolen"] for v in m["restores"]) / GiB, 1),
               "slow_s": round(m["slow_s"], 2), "glm_cold_in_state": [c for c in (m["state_cold"] or []) if c.startswith("glm")],
               "lost_chunks_since_last": m["lost_chunks_since_last"], "demote_left_slow_GiB_since_last": m["demote_left_slow_GiB_since_last"],
               "bursts_on_node": m["bursts_on_node"]}
              for m in all_ms.get(s, []) if m["what"] == "glm53"]
out["glm"] = glm

# 4. relay
rel = []
for i, m in sorted(rp.items()):
    f = fp.get(i)
    rel.append({"i": i, "what": m["what"], "relay_GiB": round(m["relay_GiB"], 2), "relay_cards": m["relay_cards"],
                "lat_relay": m["latency_s"], "lat_fluidpool": f["latency_s"] if f else None,
                "delta_s": round(m["latency_s"] - f["latency_s"], 3) if f else None})
used = [x for x in rel if x["relay_GiB"] > 0.01 and x["delta_s"] is not None]
unused = [x for x in rel if x["relay_GiB"] <= 0.01 and x["delta_s"] is not None]


def med(xs):
    return pct(xs, 0.5) if xs else None


out["relay"] = {"misses": len(rel), "with_relay_bytes": len(used),
                "relay_GiB_total": round(sum(x["relay_GiB"] for x in rel), 1),
                "median_delta_s_with_relay": med([x["delta_s"] for x in used]),
                "median_delta_s_without_relay": med([x["delta_s"] for x in unused]),
                "faster_with_relay": sum(1 for x in used if x["delta_s"] < 0),
                "by_model": {}, "rows": rel}
for x in used:
    k = x["what"].split("@")[0]
    b = out["relay"]["by_model"].setdefault(k, {"n": 0, "relay_GiB": 0.0, "deltas": []})
    b["n"] += 1
    b["relay_GiB"] = round(b["relay_GiB"] + x["relay_GiB"], 1)
    b["deltas"].append(x["delta_s"])
for k, b in out["relay"]["by_model"].items():
    b["median_delta_s"] = med(b.pop("deltas"))

json.dump(out, open(os.path.join(d, "tail_analysis.json"), "w"), indent=1, default=str)
for s, v in out["systems"].items():
    print(s, json.dumps({k: v[k] for k in ("misses", "mean_s", "p95_s", "p99_s", "cold_frac", "slow_GiB_total",
                                           "share_of_miss_time_explained_by_slow_bytes", "slow_bound_misses",
                                           "burst_p95_s", "burst_tail_slow_bound_share", "burst_tail_time_explained_by_slow")}))
print("tail", json.dumps({s: v["tail_ge_p95"] for s, v in out["systems"].items()})[:3000])
print("outlier", json.dumps(out.get("outlier"), default=str)[:4000])
print("glm", json.dumps(glm)[:3000])
print("relay", json.dumps({k: v for k, v in out["relay"].items() if k != "rows"}))
