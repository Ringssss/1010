"""Tables for the random-burst run from each system's run.log.
usage: summarize_maas3.py <dir>   -> prints markdown, writes <dir>/comparison.md and comparison.json

Rows are groups of the measured requests (the same request indices for every system); the latency
is the restore time (latency_s) and, in the second table, restore + making room on the cards
(total_s). Hits carry the calibrated IPC attach cost, so "misses only" is where systems differ."""
import collections
import json
import os
import sys

d = sys.argv[1]
SYS = ["fluidpool", "fluidpool_relay", "fluidpool_v2", "fluidpool_v2_norelay", "fluidpool_v2_noprestage",
       "fluidpool_v2_nohbmfirst", "fluidpool_v2_noplan", "fluidpool_v2_nodemote", "fluidpool_v2_age",
       "fluidpool_noplan", "fluidpool_static", "fluidpool_nodemote", "fluidpool_nostripe", "fluidpool_age",
       "janus", "sglang"]
F = "fluidpool"
KINDS = ["dram", "ib", "ssd_rd", "ssd_wr", "hbm_idle", "hbm_serve", "h2d"]


def load(s):
    p = os.path.join(d, s, "run.log")
    if not os.path.exists(p):
        return None
    rows = []
    for line in open(p):
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    starts = [k for k, r in enumerate(rows) if r.get("event") == "run_start"]
    return rows[starts[-1]:] if starts else rows                    # the last run appended to the log


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def stat(rs, key):
    xs = [r[key] for r in rs]
    if not xs:
        return None
    return {"n": len(xs), "mean": round(sum(xs) / len(xs), 3), "p50": round(pct(xs, .5), 3), "p95": round(pct(xs, .95), 3),
            "p99": round(pct(xs, .99), 3), "max": round(max(xs), 3)}


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


ev = {s: load(s) for s in SYS}
ev = {s: v for s, v in ev.items() if v}
req = {s: [r for r in v if r.get("event") == "request" and r["phase"] != "warmup"] for s, v in ev.items()}
F = F if F in req else next((x for x in req if x.startswith("fluidpool")), F)   # the reference system
miss = lambda r: r["kind"] != "ipc_hit"
on = lambda r: bool(r.get("burst_kinds_on_node"))
GROUPS = collections.OrderedDict([
    ("all requests", lambda r: True),
    ("misses only", miss),
    ("calm, all", lambda r: r["phase"] == "calm"),
    ("calm, misses", lambda r: r["phase"] == "calm" and miss(r)),
    ("burst part, all", lambda r: r["phase"] == "burst"),
    ("burst part, misses", lambda r: r["phase"] == "burst" and miss(r)),
    ("misses, a burst on the node", lambda r: r["phase"] == "burst" and miss(r) and on(r)),
    ("misses, no burst on the node", lambda r: r["phase"] == "burst" and miss(r) and not on(r)),
    ("misses, burst fired in flight", lambda r: miss(r) and bool(r.get("during_bursts"))),
] + [(f"misses, {k} on the node", (lambda k: lambda r: miss(r) and k in (r.get("burst_kinds_on_node") or []))(k)) for k in KINDS])

out = {"latency_s": {}, "total_s": {}, "kinds": {}, "warm_servable": {}, "tenants": {}, "reaction": {}, "graph": {},
       "integrity": {}}
md = []


def table(key, title):
    md.append(f"\n### {title}\n")
    cmp_ = [b for b in ("janus", "sglang", "fluidpool_relay", "fluidpool_v2") if b in req and b != F]
    md.append("| requests | n | " + " | ".join(f"{s} mean / p50 / p95 / p99 (s)" for s in req) +
              "".join(f" | {b} / {F} (mean, p95)" for b in cmp_) + " |")
    md.append("|---|---|" + "---|" * (len(req) + len(cmp_)))
    for g, pred in GROUPS.items():
        S = {s: stat([r for r in req[s] if pred(r)], key) for s in req}
        if not any(S.values()):
            continue
        out[key][g] = S

        def ratio(b):
            if not S.get(F) or not S.get(b):
                return "-"
            f = lambda k: f"{S[b][k] / S[F][k]:.1f}x" if S[F][k] > 0 else "-"
            out[key][g][f"{b}_over_fluidpool"] = {"mean": round(S[b]["mean"] / S[F]["mean"], 2) if S[F]["mean"] > 0 else None,
                                                   "p95": round(S[b]["p95"] / S[F]["p95"], 2) if S[F]["p95"] > 0 else None}
            return f"{f('mean')}, {f('p95')}"
        cell = lambda x: f"{x['mean']:.2f} / {x['p50']:.2f} / {x['p95']:.2f} / {x['p99']:.2f}" if x else "-"
        n = next((x["n"] for x in S.values() if x), 0)
        ns = {x["n"] for x in S.values() if x}
        md.append(f"| {g} | {n if len(ns) == 1 else '/'.join(str(S[s]['n']) if S[s] else '0' for s in req)} | " +
                  " | ".join(cell(S[s]) for s in req) + "".join(f" | {ratio(b)}" for b in cmp_) + " |")


table("latency_s", "Activation latency: restore only")
table("total_s", "Activation latency: making room on the cards + restore")

# Cold bytes: the share of an activation's bytes read from a slow tier (durable file, local NVMe,
# a peer's NVMe/file staging) -- "partly cold" activations, beyond the binary cold-start count.
SLOW_PATHS = ("file", "lssd", "rstage")
md.append("\n### Cold bytes per activation (file + NVMe + peer staging) / all bytes, misses only\n")
md.append("| system | mean | p50 | p95 | max | activations with any cold byte | cold GiB total |")
md.append("|---|---|---|---|---|---|---|")
out["cold_bytes"] = {}
for sname in req:
    fr, gib = [], 0.0
    for r in req[sname]:
        if not miss(r):
            continue
        pg = r.get("paths_GiB") or {}
        tot = sum(pg.values()) or r.get("GiB") or 0
        cold = sum(v for k, v in pg.items() if k in SLOW_PATHS)
        if not pg and r["kind"] == "cold":
            cold = tot = r.get("GiB") or 1.0
        if tot:
            fr.append(cold / tot)
            gib += cold
    if fr:
        st = {"mean": round(sum(fr) / len(fr), 4), "p50": pct(fr, 0.5), "p95": pct(fr, 0.95), "max": max(fr),
              "any": sum(1 for x in fr if x > 0.001), "n": len(fr), "GiB": round(gib, 1)}
        out["cold_bytes"][sname] = st
        md.append(f"| {sname} | {st['mean']:.3f} | {st['p50']:.3f} | {st['p95']:.3f} | {st['max']:.3f} | "
                  f"{st['any']}/{st['n']} | {st['GiB']} |")

md.append("\n### How each request was served\n")
md.append("| requests | " + " | ".join(req) + " |")
md.append("|---|" + "---|" * len(req))
for g in ("all requests", "calm, all", "burst part, all"):
    cells = []
    for s in req:
        c = collections.Counter(r["kind"] for r in req[s] if GROUPS[g](r))
        out["kinds"].setdefault(g, {})[s] = dict(c)
        cells.append(", ".join(f"{k} {v}" for k, v in sorted(c.items())))
    md.append(f"| {g} | " + " | ".join(cells) + " |")
md.append("")
md.append("| | " + " | ".join(req) + " |")
md.append("|---|" + "---|" * len(req))
md.append("| cold activations, burst part | " + " | ".join(str(sum("cold" in r["kind"] for r in req[s] if r["phase"] == "burst")) for s in req) + " |")
md.append("| requests that needed a fallback path | " + " | ".join(str(sum(bool(r.get("fallback")) for r in req[s])) for s in req) + " |")

md.append("\n### Share of (model, home) pairs that can be activated without a cold read\n")
md.append("| part | " + " | ".join(f"{s} pairs mean / min, bytes mean / min" for s in ev) + " |")
md.append("|---|" + "---|" * len(ev))
for ph in ("calm", "burst"):
    cells = []
    for s in ev:
        st = [r for r in ev[s] if r.get("event") == "state" and r["phase"] == ph]
        x = {"n": len(st), "pairs_mean": mean([r["pairs"] for r in st]), "pairs_min": min((r["pairs"] for r in st), default=None),
             "bytes_mean": mean([r["bytes"] for r in st]), "bytes_min": min((r["bytes"] for r in st), default=None),
             "fast_coverage_mean": mean([r.get("fast_coverage") for r in st]),
             "fast_coverage_min": min((r["fast_coverage"] for r in st if r.get("fast_coverage") is not None), default=None)}
        out["warm_servable"].setdefault(ph, {})[s] = x
        cells.append(f"{x['pairs_mean']} / {x['pairs_min']}, {x['bytes_mean']} / {x['bytes_min']}" if st else "-")
    md.append(f"| {ph} | " + " | ".join(cells) + " |")

md.append("\n### What the tenants got (per burst kind)\n")
md.append("| kind | bursts | " + " | ".join(f"{s}: obtained share, time to obtain mean / p95 (s)" for s in ev) + " |")
md.append("|---|---|" + "---|" * len(ev))
bends = {s: [r for r in ev[s] if r.get("event") == "burst_end"] for s in ev}
for k in KINDS:
    cells, n = [], 0
    for s in ev:
        bs = [b for b in bends[s] if b["kind"] == k]
        n = max(n, len(bs))
        ten = [b["tenant"] for b in bs if b.get("tenant")]
        x = {"n": len(bs)}
        if ten:
            asked, got = sum(t["asked_GiB"] for t in ten), sum(t["obtained_GiB"] for t in ten)
            ts = [t["obtain_s"] for t in ten]
            x.update(obtained_share=round(got / max(asked, 1e-9), 3), obtain_s_mean=mean(ts), obtain_s_p95=pct(ts, .95),
                     evict_s_mean=mean([b.get("evict_s") for b in bs]))
            cells.append(f"{x['obtained_share']:.2f}, {x['obtain_s_mean']:.2f} / {x['obtain_s_p95']:.2f}")
        else:
            g = [b["traffic"].get("GBps") for b in bs if b.get("traffic")]
            x.update(traffic_GBps_mean=mean(g))
            cells.append(f"traffic {x['traffic_GBps_mean']} GB/s" if bs else "-")
        out["tenants"].setdefault(k, {})[s] = x
    if n:
        md.append(f"| {k} | {n} | " + " | ".join(cells) + " |")

md.append("\n### How the system reacted while a burst was on\n")
md.append("| kind | " + " | ".join(f"{s}: bursts answered by a yield, first yield mean / p95 (s), GiB yielded" for s in ev) + " |")
md.append("|---|" + "---|" * len(ev))
for k in ("dram", "hbm_idle", "hbm_serve"):
    cells = []
    for s in ev:
        bs = [b for b in bends[s] if b["kind"] == k and b.get("reaction")]
        fy = [b["reaction"]["first_yield_s"] for b in bs if b["reaction"]["first_yield_s"] is not None]
        x = {"bursts": len(bs), "answered": len(fy), "first_yield_s_mean": mean(fy), "first_yield_s_p95": pct(fy, .95),
             "yielded_GiB": round(sum(b["reaction"]["yielded_GiB"] for b in bs), 1)}
        out["reaction"].setdefault(k, {})[s] = x
        cells.append(f"{x['answered']}/{x['bursts']}, {x['first_yield_s_mean']} / {x['first_yield_s_p95']}, {x['yielded_GiB']}" if bs else "-")
    md.append(f"| {k} | " + " | ".join(cells) + " |")

md.append("\n### Live graph and integrity\n")
md.append("| | " + " | ".join(ev) + " |")
md.append("|---|" + "---|" * len(ev))
for s in ev:
    gs = next((r for r in reversed(ev[s]) if r.get("event") == "graph_stats"), {})
    su = next((r for r in reversed(ev[s]) if r.get("event") == "summary"), {})
    v = [r for r in req[s] if r.get("verify_bad") is not None]
    mix = collections.Counter()
    for r in req[s]:
        for k, g in (r.get("paths_GiB") or {}).items():
            mix[k] += g
    tot = sum(mix.values()) or 1
    out["graph"][s] = {k: gs.get(k) for k in ("poll_ms_p50", "poll_ms_p99", "polls", "events", "slab_GiB", "fast_coverage_all")}
    out["integrity"][s] = {"requests": len(req[s]), "hits": sum(not miss(r) for r in req[s]), "misses": sum(miss(r) for r in req[s]),
                           "skipped": su.get("skipped"), "verified": len(v), "bad_chunks": sum(r["verify_bad"] for r in v),
                           "shed_fetches": sum(r.get("failed_fetches", 0) for r in req[s]),
                           "adapter_fallbacks": sorted({r["api"] for r in ev[s] if r.get("event") == "adapter_fallback"}),
                           "restore_failed": sum(r.get("event") in ("restore_failed", "wave_failed") for r in ev[s]),
                           "burst_failed": sum(r.get("event") == "burst_failed" for r in ev[s]),
                           "bytes_by_source": {k: round(g / tot, 3) for k, g in mix.most_common()}}
row = lambda name, f: md.append(f"| {name} | " + " | ".join(str(f(s)) for s in ev) + " |")
row("graph poll p50 / p99 (ms)", lambda s: f"{out['graph'][s]['poll_ms_p50']} / {out['graph'][s]['poll_ms_p99']}")
row("pool events", lambda s: out["graph"][s]["events"])
row("requests / hits / misses / skipped", lambda s: "{requests} / {hits} / {misses} / {skipped}".format(**out["integrity"][s]))
row("verified restores, bad chunks", lambda s: "{verified}, {bad_chunks}".format(**out["integrity"][s]))
row("shed fetches, failed waves, failed bursts", lambda s: "{shed_fetches}, {restore_failed}, {burst_failed}".format(**out["integrity"][s]))
row("adapter fallbacks", lambda s: ", ".join(out["integrity"][s]["adapter_fallbacks"]) or "none")
row("bytes by source", lambda s: out["integrity"][s]["bytes_by_source"])

sp = os.path.join(d, "burst_schedule.json")
if os.path.exists(sp):
    out["schedule"] = json.load(open(sp))["meta"]
    md.insert(0, "schedule: " + json.dumps(out["schedule"]))
idx = {s: [(r["i"], r["what"], miss(r)) for r in req[s]] for s in req}
out["same_requests"] = len({json.dumps(v) for v in idx.values()}) <= 1
md.insert(1 if "schedule" in out else 0, f"\nsame request sequence and hit/miss pattern in every system: {out['same_requests']}")
text = "\n".join(md)
print(text)
try:
    open(os.path.join(d, "comparison.md"), "w").write(text + "\n")
    json.dump(out, open(os.path.join(d, "comparison.json"), "w"), indent=1)
except OSError as e:
    print("could not write:", e)
