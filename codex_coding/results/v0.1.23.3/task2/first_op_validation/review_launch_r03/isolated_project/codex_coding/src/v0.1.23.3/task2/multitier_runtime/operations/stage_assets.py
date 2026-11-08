"""Update only a quiet task-owned source stage, preserving every previous file."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    package = json.loads(sys.stdin.read())
    spec = package['spec']
    if Path('/proc/sys/kernel/random/boot_id').read_text().strip() != spec['boot_id']:
        raise ValueError('boot changed')
    ci = json.loads(subprocess.run(['docker', 'inspect', spec['container_id']], capture_output=True,
                                 check=True, timeout=5).stdout)[0]
    if ci['Id'] != spec['container_id'] or ci['State']['StartedAt'] != spec['container_started_at'] or not ci['State']['Running']:
        raise ValueError('task container identity changed')
    raw = Path('/proc/'+str(ci['State']['Pid'])+'/cgroup').read_text()
    rel = next(l.split(':',2)[2] for l in raw.splitlines() if l.startswith('0::'))
    if (Path('/sys/fs/cgroup')/rel.lstrip('/')/'pids.current').read_text().strip() != '1':
        raise ValueError('source update only before worker startup')
    stage = Path(spec['stage'])
    if stage.parent != Path('/tmp/fluidckpt_three_node') or stage.name != spec['run']+'_n'+spec['node'][-2:]:
        raise ValueError('own source stage mismatch')
    prior = {r['relative_path']:r['sha256'] for r in spec['assets']}
    archive = stage/'source_snapshots'/package['revision']
    archive.mkdir(parents=True, exist_ok=False)
    changed = []
    for item in package['assets']:
        rel = Path(item['relative_path'])
        if rel.is_absolute() or '..' in rel.parts or not str(rel).startswith(('main_lib/fluidckpt/', 'codex_coding/')):
            raise ValueError('source asset outside task namespace')
        dest = stage/'project'/rel
        raw = base64.b64decode(item['base64'], validate=True)
        if hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('new asset SHA mismatch')
        before = hashlib.sha256(dest.read_bytes()).hexdigest() if dest.exists() else None
        if before != prior.get(str(rel)):
            raise ValueError('original stage source changed '+str(rel))
        if before == item['sha256']:
            continue
        if before is not None:
            backup = archive/rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(dest.read_bytes())
        dest.parent.mkdir(parents=True, exist_ok=True)
        temporary = dest.with_name(dest.name+'.'+package['revision']+'.tmp')
        with temporary.open('xb') as stream:
            stream.write(raw)
        temporary.replace(dest)
        changed.append({'relative_path':str(rel),'before_sha256':before,'after_sha256':item['sha256']})
    (archive/'manifest.json').write_text(json.dumps(changed,indent=2)+'\n')
    print(json.dumps({'changed':changed,'container_id':ci['Id'],'GPU_launches':0}))


if __name__ == '__main__':
    main()
