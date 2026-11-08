"""Read-only admission and retained-snapshot audit for the next experiment stage."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[5]


def main(args):
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    state = json.loads(args.session.read_text())
    root = json.loads(Path(state['root_session']).read_text())
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def inventory(ep):
        with opener.open(ep+'/inventory?compact=1', timeout=15) as response:
            return json.load(response)
    with ThreadPoolExecutor(22) as pool:
        workers = list(pool.map(inventory, root['endpoints']))
    assert [w['worker'] for w in workers] == list(range(len(workers)))
    assert not any(w['transactions'] for w in workers)
    valid = {r['id']:r for w in workers for r in w['replicas'] if r['state']=='VALID'}
    models = {}
    for name, model in state['models'].items():
        wanted = model['source_ids']
        missing = sorted(set(wanted)-valid.keys())
        expected = sum(s['n'] for s in model['sources'])
        actual = sum(valid[r]['n'] for r in wanted if r in valid)
        models[name] = {'missing':missing,'expected_bytes':expected,'available_bytes':actual,
                        'source_count':len(wanted),'coverage':[]}
        for source in model['sources']:
            regions = sorted((valid[r]['off'],valid[r]['off']+valid[r]['n']) for r in wanted
                             if r in valid and valid[r]['identity']==source['identity'] and valid[r]['tp']==source['tp'])
            cursor = 0
            for begin,end in regions:
                assert begin <= cursor, (name,source['tp'],cursor,begin)
                cursor = max(cursor,end)
            models[name]['coverage'].append({'rank':source['tp'],'bytes':cursor,'expected':source['n']})
            assert cursor==source['n']
        assert not missing and actual == expected
    evidence = {'development':'v0.1.23.3','task':'task2','created_at':datetime.now(timezone.utc).isoformat(),
        'status':'passed','scope':'read-only retained catalog and idle transactions; contents verified by subsequent restores',
        'session':str(args.session.resolve()),'session_sha256':hashlib.sha256(args.session.read_bytes()).hexdigest(),
        'models':models,'workers':workers,
        'incident':{'operation':'GET /health on parked Qwen endpoint32540',
                    'observed':'health attempted forward with meta weights; HIPGuard assertion and scheduler SIGQUIT at 2026-10-06 10:30:02',
                    'action':'use non-inference state checks and new engine restore; never claim parked session implies live service'}}
    (out/'summary.json').write_text(json.dumps(evidence,indent=2))
    log = ROOT/'codex_coding/results/glm_roundtrip/full_distributed02/model_B_node27_reclaim02.log'
    (out/'parked_health_failure_tail.txt').write_text(log.read_text(errors='replace')[-18000:])
    print(json.dumps({'status':'passed','workers':len(workers),
                      'models':{k:{a:b for a,b in v.items() if a!='coverage'} for k,v in models.items()}}))


if __name__ == '__main__':
    p=argparse.ArgumentParser(); p.add_argument('--session',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    main(p.parse_args())
