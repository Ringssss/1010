"""Start/stop only a recorded experiment process group inside its owning container."""
import argparse
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import time


def start_time(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()[19]
    except FileNotFoundError:
        return None


def main(args):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    path = Path(args.spec)
    spec = json.loads(path.read_text())
    state = path.with_suffix(".pid.json")
    if args.action == "start":
        if state.exists(): raise ValueError("process state already exists")
        with open(spec["log"], "w") as log:
            child = subprocess.Popen(spec["command"], cwd=spec["cwd"], env=dict(os.environ, **spec["env"]),
                                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        result = {"pid": child.pid, "start_time": start_time(child.pid), "spec": str(path)}
        state.write_text(json.dumps(result))
        os.chmod(state, 0o666)
        print(json.dumps(result))
    elif args.action == "stop":
        if not state.exists(): return
        data = json.loads(state.read_text())
        pid = data["pid"]
        stamp = start_time(pid)
        if stamp is not None and stamp != data["start_time"]:
            raise RuntimeError("PID reused; refusing to signal")
        if stamp is not None:
            os.killpg(pid, signal.SIGTERM)
            deadline = time.monotonic()+20
            while time.monotonic() < deadline and start_time(pid) is not None:
                try:
                    if Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].startswith("Z "): break
                except FileNotFoundError: break
                time.sleep(0.2)
            try: os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError: pass
        print(json.dumps({"stopped": pid}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop"])
    parser.add_argument("--spec", required=True)
    main(parser.parse_args())
