
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
