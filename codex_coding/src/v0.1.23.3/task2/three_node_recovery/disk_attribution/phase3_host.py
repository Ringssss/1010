"""Read exact owned Docker layer size and container-local keeper metadata."""
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import subprocess

OWN = ["0deafa1b72721d0ca8d27634f9f2bf71021d5900e631680baa41c88ff0f1fd8c", "177035641ac22e59854da0edd9af0941d7599c1820cf686491fea5a2a06c962c"]

def command(argv, seconds=5, stdin=None):
    row = {"argv": argv, "timeout_s": seconds, "started_utc": datetime.now(timezone.utc).isoformat()}
    if stdin is not None:
        row["stdin_source_sha256"] = hashlib.sha256(stdin).hexdigest()
    try:
        result = subprocess.run(argv, input=stdin, capture_output=True, timeout=seconds)
        row.update(returncode=result.returncode, stdout=result.stdout.decode(errors="replace"), stderr=result.stderr.decode(errors="replace"))
    except subprocess.TimeoutExpired as exc:
        decode = lambda value: value.decode(errors="replace") if isinstance(value, bytes) else value or ""
        row.update(returncode=None, timed_out=True, stdout=decode(exc.stdout), stderr=decode(exc.stderr), automatic_own_cli_timeout_kill_attempted=True)
    row["ended_utc"] = datetime.now(timezone.utc).isoformat()
    return row

CONTAINER_MEMORY = r'''
import json, os
from pathlib import Path
out={"keepers":[],"memory":{}}
for p in Path('/proc').iterdir():
 if not p.name.isdigit(): continue
 try:
  cmd=(p/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
  if 'keeper' not in cmd.lower() or 'python3 -c' in cmd: continue
  row={"pid":int(p.name),"stat_before":(p/'stat').read_text(),"cmdline":cmd,"status":(p/'status').read_text(),"cgroup":(p/'cgroup').read_text(),"fd_memory_metadata":[]}
  for fd in (p/'fd').iterdir():
   try:
    target=os.readlink(fd)
    if 'memfd:' in target or target.startswith('/dev/shm/'):
     s=os.stat(fd);row['fd_memory_metadata'].append({"fd":fd.name,"target":target,"apparent_bytes":s.st_size,"allocated_bytes":s.st_blocks*512,"device":s.st_dev,"inode":s.st_ino})
   except OSError as e: row['fd_memory_metadata'].append({"fd":fd.name,"error":str(e)})
  row['stat_after']=(p/'stat').read_text();out['keepers'].append(row)
 except OSError as e: out.setdefault('errors',[]).append({"pid":p.name,"error":str(e)})
for name in ['memory.current','memory.max','memory.stat','memory.events']:
 try: out['memory'][name]=Path('/sys/fs/cgroup',name).read_text()
 except OSError as e: out['memory'][name]={"error":str(e)}
out['meminfo']=Path('/proc/meminfo').read_text()
print(json.dumps(out))
'''

if os.uname().nodename != "m03r2n08":
    raise SystemExit("node28 actual hostname mismatch")
out = {"hostname": os.uname().nodename, "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(), "utc": datetime.now(timezone.utc).isoformat(), "remote_writes": 0, "gpu_operations": 0, "signals_to_services": 0, "owned": []}
for cid in OWN:
    row = {"id": cid}
    row["size_inspect"] = command(["docker", "inspect", "--size", "--format", '{"Id":{{json .Id}},"Name":{{json .Name}},"Pid":{{json .State.Pid}},"StartedAt":{{json .State.StartedAt}},"Running":{{json .State.Running}},"SizeRw":{{json .SizeRw}},"SizeRootFs":{{json .SizeRootFs}},"PidMode":{{json .HostConfig.PidMode}}}', cid], 20)
    print(json.dumps({"completed_size":cid,"result":row["size_inspect"]}),flush=True)
    if row["size_inspect"].get("returncode") != 0:
        out["owned"].append(row)
        continue
    data = json.loads(row["size_inspect"]["stdout"])
    if data["Id"] != cid or not data["Running"]:
        raise SystemExit("owned container identity mismatch")
    row["top"] = command(["docker", "top", cid, "-eo", "pid,ppid,stat,rss,args"])
    row["local_directories_allocated"] = command(["docker", "exec", "-u", "0", cid, "timeout", "15s", "du", "-x", "-B1", "--max-depth=2", "--", "/tmp", "/root", "/var/log"], 18)
    row["keeper_memory"] = command(["docker", "exec", "-i", "-u", "0", cid, "timeout", "7s", "python3", "-"], 10, stdin=CONTAINER_MEMORY.encode())
    out["owned"].append(row)
    print(json.dumps({"completed":cid,"local_du":row["local_directories_allocated"]}),flush=True)
out["shm_df"] = command(["df", "-B1", "--", "/dev/shm"])
out["root_df_end"] = command(["df", "-B1", "--", "/var/lib/docker"])
print("FINAL_JSON")
print(json.dumps(out, indent=2))
