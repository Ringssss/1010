"""Build the comparison tables from the three systems' raw run logs.
usage: summarize_maas.py <maas_dir>     (expects <maas_dir>/{fluidpool,janus,sglang}/run.log)
Writes <maas_dir>/comparison.json and prints markdown tables. Nothing here is estimated: every
number is an aggregate of the `restore_event` lines."""
import collections, json, os, sys

d = sys.argv[1]
SYS = ["fluidpool", "janus", "sglang"]
ev = {}
for s in SYS:
    p = os.path.join(d, s, "run.log")
    if not os.path.exists(p):
        continue
    rows = [json.loads(l) for l in open(p) if '"restore_event"' in l]
    # a re-run appends: keep the last complete pass
    last0 = max(i for i, r in enumerate(rows) if r["i"] == 0)
    ev[s] = [r for r in rows[last0:] if r["phase"] != "warmup"]

def pct(xs, q):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(q * len(xs)))]

def stats(rs):
    lat = [r["latency_s"] for r in rs]; gib = sum(r["GiB"] for r in rs)
    return {"n": len(rs), "mean_s": round(sum(lat) / len(lat), 3), "p50_s": round(pct(lat, .5), 3),
            "p95_s": round(pct(lat, .95), 3), "max_s": round(max(lat), 3), "sum_s": round(sum(lat), 2),
            "GiB": round(gib, 1), "GBps": round(gib * 2**30 / 1e9 / sum(lat), 2),
            "kinds": dict(collections.Counter(r["kind"] for r in rs))}

phases = []
for s in ev:
    for r in ev[s]:
        if r["phase"] not in phases: phases.append(r["phase"])
out = {"phases": {}, "per_model": {}, "integrity": {}}
print("| phase | n | " + " | ".join(f"{s} mean / p95 (s)" for s in ev) + " | vs janus | vs sglang |")
print("|---|---|" + "---|" * (len(ev) + 2))
for ph in phases + ["ALL"]:
    row, st = [], {}
    for s in ev:
        rs = [r for r in ev[s] if ph == "ALL" or r["phase"] == ph]
        st[s] = stats(rs)
        row.append(f"{st[s]['mean_s']:.2f} / {st[s]['p95_s']:.2f}")
    out["phases"][ph] = st
    f = st.get("fluidpool")
    ratio = lambda b: f"{st[b]['mean_s'] / f['mean_s']:.1f}x" if f and b in st else "-"
    print(f"| {ph} | {f['n'] if f else '-'} | " + " | ".join(row) + f" | {ratio('janus')} | {ratio('sglang')} |")
print()
print("| phase | " + " | ".join(f"{s}: how restores were served" for s in ev) + " |")
print("|---|" + "---|" * len(ev))
for ph in phases + ["ALL"]:
    print(f"| {ph} | " + " | ".join(", ".join(f"{k} {v}" for k, v in sorted(out['phases'][ph][s]['kinds'].items())) for s in ev) + " |")
print()
models = sorted({r["model"] for s in ev for r in ev[s]}, key=lambda m: next(r["GiB"] for s in ev for r in ev[s] if r["model"] == m))
print("| model | GiB | " + " | ".join(f"{s} n / mean / max (s)" for s in ev) + " |")
print("|---|---|" + "---|" * len(ev))
for m in models:
    cells = []
    for s in ev:
        rs = [r for r in ev[s] if r["model"] == m]
        st = stats(rs) if rs else None
        out["per_model"].setdefault(m, {})[s] = st
        cells.append(f"{st['n']} / {st['mean_s']:.2f} / {st['max_s']:.2f}" if st else "-")
    gib = next(r["GiB"] for s in ev for r in ev[s] if r["model"] == m)
    print(f"| {m} | {gib} | " + " | ".join(cells) + " |")
for s in ev:
    ver = [r for r in ev[s] if r.get("verify_bad") is not None]
    out["integrity"][s] = {"restores": len(ev[s]), "verified_from_HBM": len(ver), "bad_chunks": sum(r["verify_bad"] for r in ver),
                           "failed_fetches_shed": sum(r["failed_fetches"] for r in ev[s])}
print("\nintegrity:", json.dumps(out["integrity"]))
# the fluid pool's path mix: where the bytes came from
mix = collections.Counter()
for r in ev.get("fluidpool", []):
    for k, g in r["paths"].items():
        mix[k.split(":")[0] if not k.startswith("dram:") else "remote dram"] += g
tot = sum(mix.values()) or 1
out["fluid_bytes_by_source"] = {k: round(v / tot, 3) for k, v in mix.most_common()}
print("fluidpool bytes by source:", out["fluid_bytes_by_source"])
json.dump(out, open(os.path.join(d, "comparison.json"), "w"), indent=1)
