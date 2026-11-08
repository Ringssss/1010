"""Validate completed source format metadata and frozen SHA without rereading 70 GB."""
import argparse
import json
from pathlib import Path
import resource

from full_protocol import check_prepared, frozen_rank_hashes

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
p = argparse.ArgumentParser()
p.add_argument("--session", type=Path, required=True)
p.add_argument("--prepared-data", type=Path, required=True)
p.add_argument("--frozen-source", type=Path, required=True)
a = p.parse_args()
model = json.loads(a.session.read_text())["models"]["B"]
prepared = json.loads(a.prepared_data.read_text())
if prepared.get("status") != "passed":
    raise ValueError("format generation is not complete and accepted")
frozen = frozen_rank_hashes(a.frozen_source, model)
check_prepared(prepared, model, frozen)
print(json.dumps({"status": "passed", "ranks": sorted(frozen), "logical_bytes": sum(r["logical_bytes"] for r in prepared["ranks"]),
                  "file_bytes": sum(r["file_bytes"] for r in prepared["ranks"]), "frozen_rank_sha256": frozen,
                  "scope": "metadata/layout/file size and source-stream full SHA evidence; no GPU"}, indent=2))
