"""Parent-only deployment of frozen small assets into its new isolated stage."""
import base64
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    bundle = json.loads(Path(sys.argv[1]).read_text())
    if os.uname().nodename != bundle["hostname"] or Path("/proc/sys/kernel/random/boot_id").read_text().strip() != bundle["boot_id"]:
        raise ValueError("host identity differs")
    fmt = '{"id":{{json .Id}},"started":{{json .State.StartedAt}},"running":{{json .State.Running}},"image":{{json .Image}}}'
    done = subprocess.run(["docker", "inspect", "--format", fmt, bundle["container_id"]], capture_output=True, timeout=4)
    body = json.loads(done.stdout) if done.returncode == 0 else {}
    if body != {"id": bundle["container_id"], "started": bundle["container_started_at"], "running": True,
                "image": bundle["image_id"]}:
        raise ValueError("container exact identity differs")
    stage = Path(bundle["stage"])
    if stage.parent != Path("/tmp/fluidckpt_three_node") or stage.name != "minimal_measure_r01_n" + bundle["node"][-2:]:
        raise ValueError("deployment outside the new owned measurement stage")
    rows = []
    for asset in bundle["assets"]:
        rel = Path(asset["relative_path"])
        if rel.is_absolute() or ".." in rel.parts or not str(rel).startswith("codex_coding/"):
            raise ValueError("small deployment asset outside codex_coding")
        data = base64.b64decode(asset["base64"], validate=True)
        if hashlib.sha256(data).hexdigest() != asset["sha256"]:
            raise ValueError("asset SHA differs")
        target = stage / "project" / rel
        before = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
        if before != asset.get("expected_before_sha256"):
            raise ValueError("stage asset changed since frozen setup: " + str(rel))
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".minimal_deploy.tmp")
        with temporary.open("xb") as handle:
            handle.write(data)
        temporary.replace(target)
        after = hashlib.sha256(target.read_bytes()).hexdigest()
        if after != asset["sha256"]:
            raise ValueError("stage readback SHA differs")
        rows.append({"relative_path": str(rel), "before_sha256": before, "after_sha256": after, "bytes": len(data)})
    print(json.dumps({"node": bundle["node"], "identity": body, "assets": rows, "gpu_calls": 0,
                      "scope": "new stage source/config deployment only"}, indent=2))


if __name__ == "__main__":
    main()
