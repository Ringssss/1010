"""Read-only host collection; the unchanged Docker GET helper is prepended by the wrapper."""
import signal


def limited_text(filename, limit=1 << 20):
    try:
        with Path(filename).open('rb') as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('metadata exceeds byte bound: ' + str(filename))
        return raw.decode().strip()
    except OSError as exc:
        return {'error': repr(exc)}


def cgroup_for(pid):
    raw = limited_text(f'/proc/{pid}/cgroup', 16384)
    if not isinstance(raw, str):
        return {'raw': raw}
    rel = next((line.split(':', 2)[2] for line in raw.splitlines() if line.startswith('0::')), None)
    if rel is None:
        return {'raw': raw, 'v2': False}
    root = Path('/sys/fs/cgroup') / rel.lstrip('/')
    fields = {key: limited_text(root / key, 65536) for key in
              ('memory.current', 'memory.peak', 'memory.max', 'memory.events', 'memory.stat', 'pids.current')}
    stat = fields['memory.stat']
    parsed = {line.split()[0]: int(line.split()[1]) for line in stat.splitlines()} if isinstance(stat, str) else {}
    return {'raw': raw, 'v2': True, 'path': str(root), 'fields': fields,
            'stat_bytes': {key: parsed.get(key) for key in ('anon', 'file', 'shmem', 'file_mapped', 'file_dirty', 'file_writeback')}}


def model_metadata(directory):
    root = Path(directory).resolve()
    result = {'path': str(root), 'exists': root.is_dir(), 'payload_read_bytes': 0, 'files': {}, 'shards': []}
    if not root.is_dir():
        return result
    index = None
    for name in ('config.json', 'model.safetensors.index.json', 'generation_config.json'):
        path = root / name
        if not path.exists():
            result['files'][name] = {'exists': False}
            continue
        if path.resolve().parent != root:
            raise ValueError('model metadata symlink leaves selected root')
        with path.open('rb') as stream:
            raw = stream.read((1 << 20) + 1)
        if len(raw) > 1 << 20:
            raise ValueError('model metadata exceeds byte bound')
        data = json.loads(raw)
        result['files'][name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        if name == 'config.json':
            result['files'][name]['config'] = data
        elif name == 'model.safetensors.index.json':
            index = data
            result['files'][name].update(metadata=data.get('metadata'), tensor_names=len(data['weight_map']))
    if index is not None:
        names = sorted(set(index['weight_map'].values()))
        if len(names) > 64:
            raise ValueError('model shard count exceeds bound')
        for name in names:
            if Path(name).name != name or not name.endswith('.safetensors'):
                raise ValueError('invalid shard metadata path')
            path = root / name
            if path.resolve().parent != root:
                raise ValueError('shard symlink leaves selected root')
            stat = path.stat()
            result['shards'].append({'name': name, 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns})
        result['checkpoint_file_bytes'] = sum(row['bytes'] for row in result['shards'])
    return result


def numa_memory(spec):
    selected = sorted({role['numa'] for role in spec['roles']} | set(spec.get('source_numa_nodes', [])))
    nodes = list(range(8)) if spec.get('inspect_all_numa') else selected
    if any(type(node) is not int or node < 0 or node > 7 for node in nodes):
        raise ValueError('NUMA metadata scope must be fixed numeric nodes0..7')
    result = {}
    for node in nodes:
        raw = limited_text(f'/sys/devices/system/node/node{node}/meminfo', 65536)
        fields = {}
        if isinstance(raw, str):
            for line in raw.splitlines():
                match = re.fullmatch(r'Node\s+\d+\s+([^:]+):\s+(\d+)\s+kB', line.strip())
                if match:
                    fields[match[1]] = int(match[2]) * 1024
        available = all(key in fields for key in ('MemFree', 'FilePages', 'Shmem'))
        headroom = fields['MemFree'] + fields['FilePages'] - fields['Shmem'] if available else None
        result[str(node)] = {'selected_role_numa': node in selected, 'raw': raw,
            'fields_bytes': {key: fields.get(key) for key in ('MemTotal', 'MemFree', 'MemUsed', 'FilePages', 'Shmem')},
            'free_plus_file_minus_shmem_bytes': headroom,
            'rough_rank40GiB_estimate_passed': headroom >= 40 << 30 if headroom is not None else False,
            'estimate_is_exclusive_allocation_proof': False}
    return result


def collect(spec):
    if spec['node'] not in ('node27', 'node28', 'node30') or os.uname().nodename != spec['hostname']:
        raise ValueError('selected host mismatch')
    started = time.monotonic_ns()
    body = {'node': spec['node'], 'hostname': os.uname().nodename,
            'boot_id': limited_text('/proc/sys/kernel/random/boot_id', 128),
            'gpu_api_calls': 0, 'device_opens': 0, 'gpu_resource_queries': 0,
            'remote_file_writes': 0, 'signals_sent': 0, 'container_execs': 0,
            'node29_accesses': 0, 'physical_gpu_available': None,
            'self_cgroup': cgroup_for(os.getpid())}
    meminfo = limited_text('/proc/meminfo', 65536)
    fields = {line.split(':')[0]: int(line.split()[1]) * 1024 for line in meminfo.splitlines()
              if len(line.split()) >= 2 and line.split()[1].isdigit()}
    body['host_memory'] = {key: fields.get(key) for key in ('MemTotal', 'MemAvailable', 'Cached', 'Shmem', 'SReclaimable')}
    body['host_memory_admission'] = {'minimum_available_bytes': spec['minimum_host_available_bytes'],
                                   'passed': fields.get('MemAvailable', 0) >= spec['minimum_host_available_bytes']}
    body['numa_memory'] = numa_memory(spec)
    listing = docker('/containers/json?all=1')
    if not isinstance(listing, list) or len(listing) > 64:
        raise ValueError('container inventory unavailable or exceeds bound')
    body['protected_containers'] = []
    image_ids = {spec['image_id']}
    for entry in listing:
        ci = docker('/containers/' + entry['Id'] + '/json')
        state = ci['State']
        pid = state['Pid']
        row = {'id': ci['Id'], 'name': ci['Name'], 'image_id': ci['Image'],
               'running': state['Running'], 'started_at': state['StartedAt'], 'pid': pid,
               'host_config': {key: ci['HostConfig'].get(key) for key in ('Memory', 'MemorySwap', 'PidsLimit', 'ShmSize', 'IpcMode', 'NetworkMode', 'Privileged')},
               'mounts': [{key: mount.get(key) for key in ('Source', 'Destination', 'RW')} for mount in ci['Mounts']]}
        if pid:
            row['pid_stat'] = limited_text(f'/proc/{pid}/stat', 65536)
            row['cgroup'] = cgroup_for(pid)
        body['protected_containers'].append(row)
        image_ids.add(ci['Image'])
    body['candidate_name_free'] = all(row['name'].lstrip('/') != spec['candidate_container_name'] for row in body['protected_containers'])
    body['images'] = {}
    for image_id in sorted(image_ids):
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id):
            raise ValueError('invalid image identity')
        data = docker('/images/' + image_id + '/json')
        body['images'][image_id] = {key: data.get(key) for key in ('Id', 'RepoTags', 'RepoDigests', 'Created', 'Architecture', 'Os', 'Size', 'status')}
    body['requested_image_present'] = body['images'][spec['image_id']].get('Id') == spec['image_id']
    body['selected_gpus'] = []
    for role in spec['roles']:
        if not re.fullmatch(r'0000:[0-9a-f]{2}:00\.0', role['bdf']):
            raise ValueError('invalid selected BDF')
        root = Path('/sys/bus/pci/devices') / role['bdf']
        body['selected_gpus'].append(dict(role, exists=root.is_dir(),
            safe_pci={key: limited_text(root / key, 128) for key in ('vendor', 'device', 'class', 'numa_node')}))
    ports = set()
    for name in ('tcp', 'tcp6'):
        raw = limited_text('/proc/net/' + name)
        if not isinstance(raw, str):
            raise ValueError('listener metadata unavailable')
        for line in raw.splitlines()[1:]:
            fields = line.split()
            if fields[3] == '0A':
                ports.add(int(fields[1].split(':')[1], 16))
    body['ports_free'] = {str(port): port not in ports for port in spec['ports']}
    body['original_xml_sha256'] = {}
    for filename in ('/usr/local/built-in-508-topo-input-tj-default.xml', '/opt/dtk/rccl/lib/topo_mapping_default.xml'):
        path = Path(filename)
        if path.exists() and path.stat().st_size > 2 << 20:
            raise ValueError('XML metadata exceeds bound')
        body['original_xml_sha256'][filename] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    if spec['node'] == 'node27' and spec.get('model_path'):
        body['model'] = model_metadata(spec['model_path'])
    body['elapsed_ns'] = time.monotonic_ns() - started
    return body


def online_metadata_main():
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError('metadata 25s deadline')))
    signal.alarm(25)
    print(json.dumps(collect(json.loads(sys.argv[1]))), flush=True)


if __name__ == '__main__':
    online_metadata_main()
