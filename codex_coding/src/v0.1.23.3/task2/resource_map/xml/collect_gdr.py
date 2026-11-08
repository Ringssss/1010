"""Collect CPU-only deployed GDR evidence into a fresh result directory."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--container", default="zjn-fluid-basic-1bce3897a2075b")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    script = Path(__file__).with_name("gdr_snapshot.py")
    source = script.read_bytes()
    manifest = {"started_utc": datetime.now(timezone.utc).isoformat(), "gpu_operations": 0,
                "read_only": True, "source_path": str(script.resolve()), "source_sha256": hashlib.sha256(source).hexdigest(), "commands": []}
    def collect(node):
        argv = ["docker", "exec", "-i", args.container, "python3", "-"]
        if node == "node29":
            argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "12.11.3.29", shlex.join(argv)]
        process = subprocess.run(argv, input=source, capture_output=True, timeout=240)
        directory = args.out / node
        directory.mkdir()
        if process.returncode:
            (directory / "failed.txt").write_bytes(process.stdout + process.stderr)
        else:
            payload = json.loads(process.stdout)
            for elf in payload["elf"]:
                for index, asm in enumerate(elf["disassembly"]):
                    path = directory / (elf["label"] + f"_branch_{index:02}.txt")
                    path.write_text(asm.pop("stdout"))
                    asm.update(saved_file=str(path.resolve()), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            (directory / "snapshot.json").write_text(json.dumps(payload, indent=2) + "\n")
        return {"node": node, "argv": argv, "returncode": process.returncode, "stderr": process.stderr.decode()}
    with ThreadPoolExecutor(max_workers=2) as executor:
        manifest["commands"] = list(executor.map(collect, ("node27", "node29")))
    manifest["ended_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["files"] = [{"path": str(p.resolve()), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(args.out.rglob("*")) if p.is_file()]
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"out": str(args.out), "commands": manifest["commands"], "gpu_operations": 0}))


if __name__ == "__main__":
    main()
