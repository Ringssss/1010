"""Collect read-only provenance on node27/29 into a new result directory."""
import argparse
import base64
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
    source = Path(__file__).parent
    manifest = {"started_utc": datetime.now(timezone.utc).isoformat(), "read_only": True, "gpu_operations": 0, "remote_file_writes": 0, "commands": [], "files": []}
    def collect(node):
        directory = args.out / node
        directory.mkdir()
        commands = []
        for kind in ("host", "container"):
            argv = ["python3", "-", args.container] if kind == "host" else ["docker", "exec", "-i", args.container, "python3", "-"]
            if node == "node29":
                argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "12.11.3.29", shlex.join(argv)]
            raw = (source / f"{kind}_snapshot.py").read_bytes()
            p = subprocess.run(argv, input=raw, capture_output=True, timeout=180)
            commands.append({"node": node, "kind": kind, "argv": argv, "stdin_file": str(source / f"{kind}_snapshot.py"), "stdin_sha256": hashlib.sha256(raw).hexdigest(), "returncode": p.returncode, "stderr": p.stderr.decode(errors="replace")})
            if p.returncode != 0:
                (directory / f"{kind}_failed.txt").write_bytes(p.stdout + p.stderr)
                continue
            payload = json.loads(p.stdout)
            if kind == "container":
                for entry in payload["files"]:
                    if "base64" in entry:
                        dest = directory / (entry["path"].replace("/", "_").lstrip("_") + ".original")
                        dest.write_bytes(base64.b64decode(entry.pop("base64")))
                        entry["saved_file"] = str(dest)
                for label, command in payload["commands"].items():
                    filename = "elf_" + hashlib.sha256(label.encode()).hexdigest()[:12] + ".txt"
                    (directory / filename).write_text(command.pop("stdout"))
                    command["saved_file"] = str(directory / filename)
            (directory / f"{kind}.json").write_text(json.dumps(payload, indent=2) + "\n")
        return commands
    with ThreadPoolExecutor(max_workers=2) as pool:
        for commands in pool.map(collect, ("node27", "node29")):
            manifest["commands"].extend(commands)
    for path in sorted(args.out.rglob("*")):
        if path.is_file():
            manifest["files"].append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size})
    manifest["ended_utc"] = datetime.now(timezone.utc).isoformat()
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"out": str(args.out), "commands": [{k: c[k] for k in ("node", "kind", "returncode")} for c in manifest["commands"]], "gpu_operations": 0}))

if __name__ == "__main__":
    main()
