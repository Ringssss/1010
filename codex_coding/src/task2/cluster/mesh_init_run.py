"""Initialise the keeper mesh communication group (collective op on all workers)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import keepctl

members = keepctl.parse_mesh()
out = Path("/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.2.0/mesh_init.json")
out.parent.mkdir(parents=True, exist_ok=True)
print(f"members={len(members)}", flush=True)
try:
    result = keepctl.mesh_init(members)
    rows = [{"node": m[0], "gpu": m[1], "reply": str(r)[:300]} for m, r in zip(members, result)]
    out.write_text(json.dumps({"ok": True, "rows": rows}, indent=1, ensure_ascii=False))
    print(json.dumps({"ok": True, "ready": sum(1 for r in result)}, ensure_ascii=False))
except Exception as e:
    out.write_text(json.dumps({"ok": False, "error": repr(e)[:2000]}, indent=1, ensure_ascii=False))
    print("FAILED", repr(e)[:500])
