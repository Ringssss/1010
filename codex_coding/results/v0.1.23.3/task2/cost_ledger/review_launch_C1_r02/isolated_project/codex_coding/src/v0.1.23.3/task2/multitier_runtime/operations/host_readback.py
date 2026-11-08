"""Read selected metadata without GPU APIs, RPC, or unbounded child processes."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import sys
import threading
import time


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError as exc:
        return {'error': repr(exc)}


def docker(endpoint):
    c = http.client.HTTPConnection('localhost', timeout=3)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(3)
    try:
        s.connect('/var/run/docker.sock')
        c.sock = s
        c.request('GET', endpoint)
        r = c.getresponse()
        raw = r.read((2 << 20) + 1)
        if len(raw) > 2 << 20:
            raise ValueError('bounded Docker response exceeded')
        return json.loads(raw) if r.status == 200 else {'status': r.status}
    finally:
        c.close()
        s.close()


def main():
    spec = json.loads(sys.argv[1])
    if spec['node'] not in ('node27', 'node28', 'node30') or os.uname().nodename != spec['hostname']:
        raise ValueError('host mismatch')
    def deadline():
        time.sleep(25)
        os._exit(124)
    threading.Thread(target=deadline, daemon=True).start()
    body = dict(node=spec['node'], hostname=os.uname().nodename,
                boot_id=read('/proc/sys/kernel/random/boot_id'), meminfo=read('/proc/meminfo'),
                gpu_api_calls=0, signals=[], remote_writes=0, node29_accesses=0)
    listing = docker('/containers/json?all=1')
    body['container_listing'] = [{k: v.get(k) for k in ('Id', 'Names', 'State', 'Status')} for v in listing]
    body['original_xml_sha256'] = {}
    for filename in ('/usr/local/built-in-508-topo-input-tj-default.xml', '/opt/dtk/rccl/lib/topo_mapping_default.xml'):
        path = Path(filename)
        body['original_xml_sha256'][filename] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    body['protected_containers'] = []
    selected = [r for r in listing if r['Id'] != spec.get('container_id') and
                any(('zjn' in n or 'fluid' in n) and n.lstrip('/') != spec.get('container_name') for n in r['Names'])]
    if len(selected) > 16:
        raise ValueError('protected metadata exceeds bound')
    for entry in selected:
        ci = docker('/containers/' + entry['Id'] + '/json')
        state = ci['State']
        pid = state['Pid']
        identity = {'stat': read(f'/proc/{pid}/stat'), 'cgroup': read(f'/proc/{pid}/cgroup')} if pid else None
        body['protected_containers'].append({'id': ci['Id'], 'name': ci['Name'], 'StartedAt': state['StartedAt'],
            'Running': state['Running'], 'Pid': pid, 'identity': identity})
    body['protected_owners'] = []
    for expected in spec.get('known_live_workers', []):
        pid = expected['pid']
        stat = read(f'/proc/{pid}/stat')
        tail = stat.rsplit(') ', 1)[1].split() if isinstance(stat, str) else []
        path = Path(f'/proc/{pid}/cmdline')
        body['protected_owners'].append({'pid': pid, 'state': tail[0] if tail else None,
            'start_ticks': tail[19] if tail else None, 'cgroup': read(f'/proc/{pid}/cgroup'),
            'argv_sha256': hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None})
    name = spec.get('container_name')
    ci = docker('/containers/' + name + '/json') if name else {'status': 404}
    if 'Id' in ci:
        body['container'] = {k: ci[k] for k in ('Id', 'Image', 'State', 'HostConfig', 'Mounts', 'Name')}
        pid = ci['State']['Pid']
        if pid:
            raw = read(f'/proc/{pid}/cgroup')
            body['cgroup'] = {'raw': raw}
            rel = next((l.split(':', 2)[2] for l in raw.splitlines() if l.startswith('0::')), None)
            if rel:
                root = Path('/sys/fs/cgroup') / rel.lstrip('/')
                body['cgroup']['files'] = {k: read(root / k) for k in
                    ('memory.current', 'memory.peak', 'memory.max', 'memory.events', 'pids.current')}
    else:
        body['container'] = ci
    body['selected_gpus'] = []
    for row in spec['roles']:
        root = Path('/sys/bus/pci/devices') / row['bdf']
        keys = ['vendor', 'device', 'class', 'numa_node']
        if spec['node'] in ('node28', 'node30'):
            keys += ['mem_info_vram_used', 'mem_info_vram_total']
        body['selected_gpus'].append({'bdf': row['bdf'], 'gpu': row['gpu'], **{k: read(root/k) for k in keys}})
    if spec['node'] in ('node28', 'node30'):
        body['kfd_numeric_owners'] = []
        root = Path('/sys/class/kfd/kfd/proc')
        if root.exists():
            entries = sorted((p for p in root.iterdir() if p.name.isdigit()), key=lambda p: int(p.name))
            if len(entries) > 128:
                raise ValueError('KFD owner metadata exceeds bound')
            for p in entries:
                body['kfd_numeric_owners'].append({'pid': int(p.name), 'stat': read(f'/proc/{p.name}/stat'),
                    'cgroup': read(f'/proc/{p.name}/cgroup'),
                    'numeric': {q.name: read(q) for q in p.iterdir() if re.fullmatch(r'vram_[0-9]+|pasid', q.name)}})
    body['listening_ports'] = []
    for name in ('tcp', 'tcp6'):
        raw = read('/proc/net/' + name)
        if not isinstance(raw, str):
            raise ValueError('listener metadata unavailable')
        for line in raw.splitlines()[1:]:
            fields = line.split()
            if fields[3] == '0A':
                body['listening_ports'].append(int(fields[1].split(':')[1], 16))
    body['ports_free'] = {str(p): p not in body['listening_ports'] for p in spec['ports']}
    parent = Path(spec['ssd_parent'])
    fs = os.statvfs(parent)
    body['ssd_parent'] = {'path': str(parent), 'available_bytes': fs.f_bavail*fs.f_frsize,
                          'effective_uid': os.geteuid(), 'effective_gid': os.getegid(),
                          'writable_executable': os.access(parent, os.W_OK | os.X_OK),
                          'owner_uid': parent.stat().st_uid, 'owner_gid': parent.stat().st_gid,
                          'mode': oct(parent.stat().st_mode),
                          'mountinfo': read('/proc/self/mountinfo')}
    body['source_assets'] = {}
    for item in spec.get('assets', []):
        p = Path(spec['stage']) / 'project' / item['relative_path']
        body['source_assets'][item['relative_path']] = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
    print(json.dumps(body), flush=True)


if __name__ == '__main__':
    main()
