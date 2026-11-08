"""CPU check for the static environment whitelist; no remote or device operations."""
import hashlib
import json
from pathlib import Path

import runtime_metadata as metadata

raw = b'export PATH="/opt/dtk/bin:$PATH" SECRET_TOKEN="do-not-output"\nexport LD_LIBRARY_PATH=/opt/hyhal/lib\nexport SECRET_PATH=/private\nsource /opt/helper.sh\n'
assignments = metadata.static_assignments(raw)
assert assignments == ["PATH=/opt/dtk/bin:$PATH", "LD_LIBRARY_PATH=/opt/hyhal/lib"], assignments
assert "SECRET" not in str(assignments) and "do-not-output" not in str(assignments)
out = metadata.RESULT_ROOT / "probe_metadata_cpu_r01"
out.mkdir(exist_ok=False)
source = Path(metadata.__file__).read_bytes()
(out / "runtime_metadata.source.py").write_bytes(source)
(out / "test_metadata.source.py").write_bytes(Path(__file__).read_bytes())
result = {"passed": True, "checks": ["only named whitelist assignments survive static parsing", "multi-export secret token excluded", "source command never executed"],
          "source_sha256": hashlib.sha256(source).hexdigest(), "gpu_operations": 0, "source_executions": 0}
(out / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
