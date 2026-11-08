"""Compare exact protected identities while excluding volatile proc-stat counters."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]


def read(path):
    return json.loads(path.read_text())


def identity(row):
    value = {k: row[k] for k in ('id', 'name', 'StartedAt', 'Running', 'Pid')}
    original = row.get('identity')
    if original:
        stat = original['stat'].rsplit(') ', 1)[1].split()
        value.update(start_ticks=stat[19], cgroup=original['cgroup'])
    return value


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--before-dir',type=Path,required=True)
    p.add_argument('--after-dir',type=Path,required=True)
    p.add_argument('--before-prefix',default='before_')
    p.add_argument('--after-prefix',default='after_')
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=False)
    refs,checks=[],[]
    for node in ('node28','node30','node27'):
        paths=[a.before_dir/(a.before_prefix+node)/'stdout.json',a.after_dir/(a.after_prefix+node)/'stdout.json']
        bodies=[]
        for i,path in enumerate(paths):
            raw=path.read_bytes()
            dest=a.out/('before_' if i==0 else 'after_')/node/'stdout.json'
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(raw)
            refs.append(dict(source_path=str(path.resolve()),path=str(dest.resolve()),sha256=hashlib.sha256(raw).hexdigest()))
            bodies.append(json.loads(raw))
        before,after=bodies
        bc={r['id']:identity(r) for r in before['protected_containers']}
        ac={r['id']:identity(r) for r in after['protected_containers']}
        bo={r['pid']:{k:r[k] for k in ('pid','start_ticks','cgroup','argv_sha256')} for r in before['protected_owners']}
        ao={r['pid']:{k:r[k] for k in ('pid','start_ticks','cgroup','argv_sha256')} for r in after['protected_owners']}
        checks.append(dict(node=node,boot_same=before['boot_id']==after['boot_id'],
            protected_containers_equal=bc==ac,protected_owner_identities_equal=bo==ao,
            original_host_XML_equal=before['original_xml_sha256']==after['original_xml_sha256'],
            protected_container_count=len(bc),protected_owner_count=len(bo),
            factory_XML_namespace_scope='host only; absent host copy does not prove old container copy SHA'))
    passed=all(all(row[k] for k in ('boot_same','protected_containers_equal','protected_owner_identities_equal','original_host_XML_equal')) for row in checks)
    report=dict(status='passed' if passed else 'failed',scope='exact old-owner/container and host-XML preservation',checks=checks,source_refs=refs)
    (a.out/'qualification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))
    if not passed:raise SystemExit(1)


if __name__=='__main__':main()
