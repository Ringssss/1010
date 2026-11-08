import json, os, signal, sys, time
from pathlib import Path
spec = json.loads(sys.argv[1])
if spec['node'] not in ('node28', 'node30') or os.uname().nodename != spec['hostname']:
    raise ValueError('fixed helper host identity mismatch')
signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError('helper metadata8s')))
signal.alarm(8)
boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
if boot != spec['boot_id']:
    raise ValueError('helper boot changed; new freeze required')
allowed = [(4, '0000:85:00.0')]
alternatives = [(0, '0000:09:00.0'), (1, '0000:36:00.0')]
node30_gpu5 = [(5, '0000:b5:00.0')]
roles = [(row['gpu'], row['bdf']) for row in spec['roles']]
if roles != allowed and not (spec['node']=='node28' and roles == alternatives) and not (spec['node']=='node30' and roles==node30_gpu5):
    raise ValueError('only explicit helper4/node28GPU0/1/node30GPU5 scope allowed')
gpus = []
for gpu, bdf in roles:
    root = Path('/sys/bus/pci/devices') / bdf
    values = {}
    for name in ('mem_info_vram_used', 'mem_info_vram_total'):
        with (root / name).open('rb') as stream:
            raw = stream.read(129)
        if len(raw)>128:
            raise ValueError('bounded selected PCI metadata unavailable')
        value = raw.decode().strip()
        if name.startswith('mem_info_vram') and not value.isdigit():
            raise ValueError('numeric selected VRAM metadata unavailable')
        values[name] = int(value)
    used, total = values['mem_info_vram_used'], values['mem_info_vram_total']
    if not 0 <= used <= total or total <= 0:
        raise ValueError('invalid selected VRAM counters')
    gpus.append(dict(gpu=gpu,bdf=bdf,used_bytes=used,total_bytes=total,free_bytes=total-used))
print(json.dumps(dict(node=spec['node'], hostname=spec['hostname'], boot_id=boot, selected_gpus=gpus,
    source='selected PCI sysfs only', gpu_api_calls=0, GPU_launches=0,
    remote_writes=0, owner_table_walks=0, node27_accesses=0, node29_accesses=0,
    observation_wall_ns=time.time_ns(), final_same_HIP_admission_passed=False)))
