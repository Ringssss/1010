"""Tables for the four-node run from the raw `request` lines of each system's run.log.
usage: summarize_maas2.py <dir>   -> prints markdown, writes <dir>/comparison.json"""
import collections, json, os, sys
d = sys.argv[1]; SYS = ["fluidpool", "janus", "sglang"]; ev = {}
for s in SYS:
    p = os.path.join(d, s, "run.log")
    if os.path.exists(p):
        rows = [json.loads(l) for l in open(p) if '"event": "request"' in l]
        if rows:
            last0 = max(i for i, r in enumerate(rows) if r["i"] == 0)
            ev[s] = [r for r in rows[last0:] if r["phase"] != "warmup"]
def st(rs):
    lat = sorted(r["latency_s"] for r in rs); g = sum(r["GiB"] for r in rs)
    return {"n": len(rs), "mean_s": round(sum(lat) / len(lat), 2), "p50_s": round(lat[len(lat) // 2], 2),
            "p95_s": round(lat[min(len(lat) - 1, int(.95 * len(lat)))], 2), "max_s": round(lat[-1], 2), "GiB": round(g),
            "kinds": dict(collections.Counter(r["kind"] for r in rs))}
out = {"phases": {}, "models": {}, "integrity": {}}
phases = []
for s in ev:
    for r in ev[s]:
        if r["phase"] not in phases: phases.append(r["phase"])
f = "fluidpool"
print("| phase | n | " + " | ".join(f"{s} mean / p95 (s)" for s in ev) + " | vs janus | vs sglang |"); print("|---|---|" + "---|" * (len(ev) + 2))
for ph in phases + ["ALL"]:
    S = {s: st([r for r in ev[s] if ph == "ALL" or r["phase"] == ph]) for s in ev}; out["phases"][ph] = S
    ratio = lambda b: f"{S[b]['mean_s'] / S[f]['mean_s']:.1f}x" if f in S and b in S else "-"
    print(f"| {ph} | {S[f]['n'] if f in S else '-'} | " + " | ".join(f"{S[s]['mean_s']:.2f} / {S[s]['p95_s']:.2f}" for s in ev) + f" | {ratio('janus')} | {ratio('sglang')} |")
print(); print("| phase | " + " | ".join(f"{s}: how" for s in ev) + " |"); print("|---|" + "---|" * len(ev))
for ph in phases + ["ALL"]:
    print(f"| {ph} | " + " | ".join(", ".join(f"{k} {v}" for k, v in sorted(out['phases'][ph][s]['kinds'].items())) for s in ev) + " |")
models = collections.OrderedDict()
for s in ev:
    for r in ev[s]: models.setdefault(r["what"].split("@")[0], r["GiB"])
print(); print("| model | GiB | " + " | ".join(f"{s} n / mean / max (s)" for s in ev) + " |"); print("|---|---|" + "---|" * len(ev))
for m, g in sorted(models.items(), key=lambda kv: kv[1]):
    cells = []
    for s in ev:
        rs = [r for r in ev[s] if r["what"].split("@")[0] == m]
        x = st(rs) if rs else None; out["models"].setdefault(m, {})[s] = x
        cells.append(f"{x['n']} / {x['mean_s']:.2f} / {x['max_s']:.2f}" if x else "-")
    print(f"| {m} | {g:.0f} | " + " | ".join(cells) + " |")
for s in ev:
    v = [r for r in ev[s] if r.get("verify_bad") is not None]
    mix = collections.Counter()
    for r in ev[s]:
        for k, g in r["paths_GiB"].items(): mix[k] += g
    tot = sum(mix.values()) or 1
    out["integrity"][s] = {"requests": len(ev[s]), "verified": len(v), "bad_chunks": sum(r["verify_bad"] for r in v),
                           "shed_fetches": sum(r["failed_fetches"] for r in ev[s]), "concurrent_waves": sum(1 for r in ev[s] if r["wave"] > 1),
                           "bytes_by_source": {k: round(g / tot, 3) for k, g in mix.most_common()}}
print("\nintegrity:", json.dumps(out["integrity"]))
try: json.dump(out, open(os.path.join(d, "comparison.json"), "w"), indent=1)
except OSError: pass
