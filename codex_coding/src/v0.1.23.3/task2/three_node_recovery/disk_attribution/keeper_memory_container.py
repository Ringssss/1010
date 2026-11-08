
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
