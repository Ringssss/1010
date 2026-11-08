"""Read exact full-distributed memory owner descriptors and local owned data sizes."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

CID = "0deafa1b72721d0ca8d27634f9f2bf71021d5900e631680baa41c88ff0f1fd8c"
def command(argv, seconds):
    row = {"argv": argv, "timeout_s": seconds, "started_utc": datetime.now(timezone.utc).isoformat()}
    try:
        result = subprocess.run(argv, capture_output=True, timeout=seconds)
        row.update(returncode=result.returncode, stdout=result.stdout.decode(errors="replace"), stderr=result.stderr.decode(errors="replace"))
    except subprocess.TimeoutExpired as exc:
        decode=lambda x:x.decode(errors="replace") if isinstance(x,bytes) else x or ""
        row.update(returncode=None,timed_out=True,stdout=decode(exc.stdout),stderr=decode(exc.stderr),automatic_own_cli_timeout_kill_attempted=True)
    row["ended_utc"]=datetime.now(timezone.utc).isoformat()
    return row
CODE = r'''
import os,json
from pathlib import Path
out=[]
for p in Path('/proc').iterdir():
 if not p.name.isdigit(): continue
 try:
  raw=(p/'cmdline').read_bytes();args=raw.split(b'\0')
  if not (b'fluidckpt.memory.owner' in args or b'fluidckpt.cluster.worker' in args): continue
  row={"pid":int(p.name),"cmdline":raw.replace(b'\0',b' ').decode(),"stat_before":(p/'stat').read_text(),"status":(p/'status').read_text(),"memory_fds":[]}
  for fd in (p/'fd').iterdir():
   try:
    target=os.readlink(fd)
    if 'memfd:' in target or target.startswith('/dev/shm/'):
     s=os.stat(fd);row['memory_fds'].append({"fd":fd.name,"target":target,"apparent_bytes":s.st_size,"allocated_bytes":s.st_blocks*512,"device":s.st_dev,"inode":s.st_ino})
   except OSError as e: row.setdefault('errors',[]).append(str(e))
  row['stat_after']=(p/'stat').read_text();out.append(row)
 except OSError as e: out.append({"pid":p.name,"error":str(e)})
print(json.dumps(out))
'''
if os.uname().nodename!="m03r2n08":raise SystemExit("hostname mismatch")
out={"hostname":os.uname().nodename,"boot_id":Path('/proc/sys/kernel/random/boot_id').read_text().strip(),"utc":datetime.now(timezone.utc).isoformat(),"remote_writes":0,"gpu_operations":0,"signals_to_services":0}
out['identity']=command(['docker','inspect','--format','{{.Id}} {{.State.StartedAt}} {{.State.Running}}',CID],4)
if not out['identity'].get('stdout','').startswith(CID+' 2026-10-04T01:14:43.800160031Z true'):
 raise SystemExit('owned identity mismatch')
out['full_memory_fds']=command(['docker','exec','-u','0',CID,'timeout','7s','python3','-c',CODE],10)
out['local_owned_data_allocated']=command(['docker','exec','-u','0',CID,'timeout','15s','du','-x','-B1','--max-depth=2','--','/data/node28/shared/zhujianian'],18)
out['local_owned_data_apparent']=command(['docker','exec','-u','0',CID,'timeout','15s','du','-x','--apparent-size','-B1','--max-depth=2','--','/data/node28/shared/zhujianian'],18)
print(json.dumps(out,indent=2))
