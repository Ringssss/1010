"""Static Docker archive evidence without starting a container process."""
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path
from collect_readonly import run

OUT = Path(__file__).resolve().parents[5] / "results/v0.1.23.3/task2/resource_map/dependency_check_r01"
CONTAINER = "zjn-fluid-basic-1bce3897a2075b"
result = {"scope": "read-only Docker metadata/archive and ELF; no container process, dlopen or GPU"}
inspection = json.loads(run(["docker", "inspect", CONTAINER], 3)["stdout"])[0]
pid = inspection["State"]["Pid"]
result["container"] = {"Id": inspection["Id"], "StartedAt": inspection["State"]["StartedAt"],
                       "Pid": pid, "Mounts": inspection["Mounts"], "GraphDriver": inspection["GraphDriver"]}
keys = {"PATH", "LD_LIBRARY_PATH", "ROCM_PATH", "HIP_PATH", "HYHAL_PATH", "DTKROOT",
        "HIP_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "NCCL_TOPO_FILE"}
result["image_environment_whitelist"] = dict(s.split("=", 1) for s in inspection["Config"]["Env"]
                                             if s.split("=", 1)[0] in keys)
result["container_mountinfo"] = [s for s in Path(f"/proc/{pid}/mountinfo").read_text().splitlines()
                                 if s.split()[4] in {"/", "/opt/hyhal", "/home/zhujianian", "/data/node28/shared/zhujianian"}]
result["container_root_access"] = run(["stat", "-Lc", "%F %s", f"/proc/{pid}/root/opt/dtk/hip/lib/libamdhip64.so"])
result["docker_storage_mount"] = run(["findmnt", "-T", inspection["GraphDriver"]["Data"]["MergedDir"],
                                       "-n", "-o", "TARGET,SOURCE,FSTYPE,OPTIONS"])
result["hostname_resolution"] = run(["getent", "hosts", "node29", "node28-ib0"])
result["archive_metadata"] = {}
paths = ["/opt/dtk/hip/lib/libamdhip64.so", "/opt/dtk/hip/lib/libamdhip64.so.4",
         "/opt/dtk/hip/lib/libgalaxyhip.so", "/opt/dtk/hip/lib/libgalaxyhip.so.5",
         "/opt/dtk/lib/libgalaxyhip.so", "/opt/dtk/lib/libgalaxyhip.so.5", "/opt/dtk/env.sh"]
for path in paths:
    argv = ["timeout", "4s", "docker", "cp", CONTAINER + ":" + path, "-"]
    p = subprocess.run(argv, capture_output=True, timeout=5)
    row = {"argv": argv, "rc": p.returncode, "stderr": p.stderr.decode()[:2000]}
    if p.returncode == 0:
        with tarfile.open(fileobj=io.BytesIO(p.stdout)) as archive:
            row["members"] = [{"name": m.name, "size": m.size, "symlink": m.issym(), "linkname": m.linkname}
                              for m in archive.getmembers()]
            if path.endswith("env.sh"):
                row["script"] = archive.extractfile(archive.getmembers()[0]).read().decode()
    result["archive_metadata"][path] = row
library = OUT / "libgalaxyhip.static_copy.so"
result["elf"] = run(["readelf", "-d", str(library)])
result["elf"]["stdout"] = "\n".join(s for s in result["elf"]["stdout"].splitlines()
                                     if any(x in s for x in ["NEEDED", "RPATH", "RUNPATH", "SONAME"]))
result["static_copy_sha256"] = hashlib.sha256(library.read_bytes()).hexdigest()
result["static_copy_bytes"] = library.stat().st_size
cache = run(["ldconfig", "-p", "-C", str(OUT / "container.ld.so.cache")])
cache["stdout"] = "\n".join(s for s in cache["stdout"].splitlines()
                            if any(x in s for x in ["galaxyhip", "amdhip64", "hsakmt", "hsa-runtime"]))
result["loader_cache_gpu_entries"] = cache
(OUT / "static_fallback.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"out": str(OUT / "static_fallback.json"), "static_copy_bytes": result["static_copy_bytes"]}))
