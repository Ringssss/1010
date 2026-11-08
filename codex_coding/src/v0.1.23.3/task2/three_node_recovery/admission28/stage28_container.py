"""Stage exactly three reviewed NCCL assets in node28's existing owned bind."""
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import resource
import signal

ROOT = Path("/home/zhujianian/zhujianian/fluidckpt_zjn")
ALLOW = {
    "codex_coding/src/v0.1.23.3/task2/three_node_recovery/admission28/runtime_gate.py",
    "codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_xml_2830_gdr0_r01/node28.physical.gdr0.xml",
    "codex_coding/results/v0.1.23.3/task2/three_node_recovery/probe_xml_2830_gdr0_r01/node28.manifest.json",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("node28 fixed asset stage 8s deadline")))
    signal.alarm(8)
    assert BUNDLE["node"] == "node28" and BUNDLE["root"] == str(ROOT)
    assert BUNDLE["container_id"] == "e693e946828a37dbf4daa3bc25855b1c28f88bd02cdd93ce0d2e95f33e37d3ba"
    assert os.uname().nodename == "m03r2n08"
    assert Path("/proc/sys/kernel/random/boot_id").read_text().strip() == "5313e6f8-1712-45dc-9855-4c47358b6c4b"
    assert os.readlink("/proc/self/ns/pid") == "pid:[4026541407]"
    original = ROOT / "codex_coding/src/v0.1.23.3/task2/three_node_recovery/probe/init_gate.py"
    assert sha(original.read_bytes()) == "927f543e2af1fee3e10c540d90ef98fa76f49f1dd242426a99fb3a188a850b9b"
    assert len(BUNDLE["assets"]) == 3 and {a["relative_path"] for a in BUNDLE["assets"]} == ALLOW
    prepared = []
    for asset in BUNDLE["assets"]:
        relative = Path(asset["relative_path"])
        assert not relative.is_absolute() and ".." not in relative.parts
        raw = base64.b64decode(asset["base64"], validate=True)
        assert len(raw) <= 128 << 10 and len(raw) == asset["bytes"] and sha(raw) == asset["sha256"]
        destination = ROOT / relative
        assert not destination.is_symlink()
        for ancestor in destination.parents:
            if ancestor == ROOT:
                break
            assert not ancestor.is_symlink()
        if destination.exists():
            assert destination.is_file() and sha(destination.read_bytes()) == asset["sha256"]
        prepared.append((asset, destination, raw))
    runtime = next(raw for asset, _, raw in prepared if asset["relative_path"].endswith("runtime_gate.py"))
    assert sha(runtime) == "97a740672a2c1e526ecdc80ae4592c7438ff7edddc3c81a60ab120c28feaef11"
    compile(runtime, "runtime_gate.py", "exec")
    manifest = json.loads(next(raw for asset, _, raw in prepared if asset["relative_path"].endswith("node28.manifest.json")))
    xml_raw = next(raw for asset, _, raw in prepared if asset["relative_path"].endswith("node28.physical.gdr0.xml"))
    assert manifest["node"] == "node28" and manifest["sha256"] == sha(xml_raw)
    assert manifest["input_file"] == str(ROOT / next(p for p in ALLOW if p.endswith("node28.physical.gdr0.xml")))
    rows = []
    for asset, destination, raw in prepared:
        reused = destination.exists()
        if not reused:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as handle:
                handle.write(raw)
        actual = sha(destination.read_bytes())
        assert actual == asset["sha256"]
        rows.append({"path": str(destination), "sha256": actual, "bytes": len(raw), "reused_identical": reused})
    assert sha(original.read_bytes()) == "927f543e2af1fee3e10c540d90ef98fa76f49f1dd242426a99fb3a188a850b9b"
    print(json.dumps({"passed": True, "sampled_utc": datetime.now(timezone.utc).isoformat(),
                      "hostname": os.uname().nodename, "pid_namespace": os.readlink("/proc/self/ns/pid"),
                      "node": "node28", "assets": rows, "old_gate_preserved": True,
                      "identity_scope": "container boot/PID namespace; parent full Docker ID/StartedAt guards required",
                      "gpu_operations": 0, "signals": 0}, indent=2))


if __name__ == "__main__":
    main()
