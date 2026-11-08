"""Stream existing owned artifacts and record missing dumps without losing logs."""
import io
import json
from pathlib import Path
import sys
import tarfile


def main():
    plan = json.loads(Path(sys.argv[1]).read_text())
    root,node = Path(sys.argv[2]),sys.argv[3]
    names = [Path(r['out']).name for r in plan['processes'] if r['node']==node]
    dump = 'operations_dump_'+plan['session']
    names.append(dump)
    missing = [name for name in names if not (root/name).exists()]
    raw = (json.dumps({'node':node,'requested':names,'missing':missing,'dump_missing':dump in missing})+'\n').encode()
    with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as handle:
        info = tarfile.TarInfo('collection_presence.json')
        info.size = len(raw)
        handle.addfile(info,io.BytesIO(raw))
        for name in names:
            if (root/name).exists():
                handle.add(root/name,arcname=name)


if __name__=='__main__':
    main()
