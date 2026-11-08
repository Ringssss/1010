"""Ship a script to a keeper container, then run it in a second call.

Combining "write the file" and "run it" in one shell line means a failure prints an enormous
mangled command instead of the traceback. Two steps keep the error legible.
"""
import base64
import shlex
import subprocess
import sys

ROOT = "/home/zhujianian/zhujianian/fluidckpt_zjn"
CONTAINER = {"node27": "zjn-keep-v02-n27", "node28": "zjn-keep-v02-n28", "node29": "zjn-keep-v02-n29"}


def ssh(node, cmd, timeout=3600):
    argv = ["bash", "-lc", cmd] if node == "node27" else \
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", node, cmd]
    return subprocess.check_output(argv, text=True, stderr=subprocess.STDOUT, timeout=timeout)


def dexec(node, inner, timeout=3600):
    """Run `inner` (a shell string) inside the keeper container."""
    cmd = (f"docker exec -i -w {ROOT} -e PYTHONPATH={ROOT}/main_lib "
           f"{CONTAINER[node]} bash -lc {shlex.quote('ulimit -c 0; ' + inner)}")
    return ssh(node, cmd, timeout=timeout)


def push(node, local, remote="/tmp/_x.py"):
    b64 = base64.b64encode(open(local, "rb").read()).decode()
    dexec(node, f"printf %s {shlex.quote(b64)} | base64 -d > {remote} && wc -c {remote}")


def run(node, local, argv=(), timeout=3600):
    push(node, local)
    args = " ".join(shlex.quote(str(a)) for a in argv)
    try:
        return dexec(node, f"python3 /tmp/_x.py {args}".strip(), timeout=timeout)
    except subprocess.CalledProcessError as e:
        return f"EXIT {e.returncode}\n{e.output}"


if __name__ == "__main__":
    print(run(sys.argv[1], sys.argv[2], sys.argv[3:]))
