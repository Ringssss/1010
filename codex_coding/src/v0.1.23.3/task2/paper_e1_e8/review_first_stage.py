"""Validate first-stage evidence and derive tables without rerunning GPU work."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[5]
BASE = ROOT / 'codex_coding/results/v0.1.23.3/task2/paper_e1_e8'


def read(path):
    return json.loads(path.read_text())


def main(out):
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    runs = [BASE/'e1/e1_c01_history_r01', BASE/'e2/e2_c01_history_r01',
            BASE/'e3/e3_c01_layouts_r01', BASE/'e3/e3_c01_layouts_r02']
    result = {'created_at': datetime.now(timezone.utc).isoformat(), 'historical': [], 'layouts': []}
    lines = ['# 第一阶段派生数据表', '', '由 review_first_stage.py 从原始事件生成；时间单位见各表。', '']
    for run in runs[:2]:
        manifest = read(run/'source_manifest.json')
        for path, expected in manifest['inputs'].items():
            assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest() == expected, path
        data = read(run/'analysis.json')
        lines += [f'## {data["experiment_id"]}：关键 rank 的可相加 breakdown（秒）', '',
                  '| phase | T_ready | rank | 到达偏移 | 布局 | 导出 | 接收等待 | 映射 | 执行等待 | 拷贝 | 拷贝后发布等待 | 发布后就绪 |',
                  '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
        for sample in data['samples']:
            ranks = []
            for path in sorted((ROOT/sample['metrics_directory']).glob('rank*.jsonl')):
                events = [json.loads(line) for line in path.read_text().splitlines()]
                names = ['load_begin', 'model_layout_end', 'target_exported', 'target_accepted',
                         'target_mapped', 'target_execute_begin', 'target_copy_complete',
                         'target_ready_publish', 'weights_ready']
                selected = {}
                for name in names:
                    hits = [e for e in events if e['event'] == name]
                    assert len(hits) == 1, (path, name)
                    selected[name] = hits[0]
                times = [selected[n]['monotonic_ns'] for n in names]
                assert times == sorted(times)
                ranks.append({'rank': selected['load_begin']['rank'], 'host': selected['load_begin']['host'],
                              'begin_ns': times[0], 'end_ns': times[-1],
                              'segments_s': [(b-a)/1e9 for a, b in zip(times, times[1:])],
                              'events': selected})
            assert len(ranks) == 2 and len({r['host'] for r in ranks}) == 1
            first = min(r['begin_ns'] for r in ranks)
            critical = max(ranks, key=lambda r: r['end_ns'])
            offset = (critical['begin_ns']-first)/1e9
            total = (critical['end_ns']-first)/1e9
            assert abs(total-sample['T_ready_s']) < 1e-9
            assert abs(offset+sum(critical['segments_s'])-total) < 1e-9
            source = sample.get('source_bytes')
            ssd = None if source is None else sum(v for k, v in source.items() if '/ssd/' in k)
            row = {'experiment':data['experiment_id'], 'phase':sample['phase'], 'ranks':ranks,
                   'critical_rank':critical['rank'], 'arrival_offset_s':offset, 'T_ready_s':total,
                   'selected_ssd_bytes_derived':ssd, 'source_bytes_total':None if source is None else sum(source.values()),
                   'ssd_kernel_read_bytes':sample.get('ssd_kernel_read_bytes')}
            result['historical'].append(row)
            values = [offset]+critical['segments_s']
            lines.append(f'| {sample["phase"]} | {total:.9f} | {critical["rank"]} | '+
                         ' | '.join(f'{v:.9f}' for v in values)+' |')
        lines += ['']
    data = read(runs[-1]/'summary.json')
    assert data['status'] == 'passed' and not data['cleanup_errors'] and not data['source_hash_mismatches']
    assert data['protected_before'] == data['protected_after']
    lines += ['## e3：执行与校验发布分开（毫秒）', '',
              '| 布局 | 目标 | prepare | execute | fill | verify/publish | 完整发布 | HBM来源MiB | DRAM来源MiB |',
              '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for sample in data['baseline']+data['observations']:
        f = sample['flow']
        assert f['verification']['passed'] and f['verification']['checked_bytes'] == 536870946
        assert sum(f['source_bytes'].values()) == 536870946
        assert f['plan']['strategy'] == 'resource_lp' and not f['plan']['fallback_reason']
        fill = f['prepare_s']+f['execute_wall_s']
        assert abs(fill-sample['data_fill_s']) < 1e-9
        hbm = sum(v for k, v in f['source_bytes'].items() if k.startswith('cache/'))
        kinds = Counter()
        for w in f['execution']:
            for op in w['records']:
                if op['role'] in ('both', 'source'):
                    kinds[op['kind']] += op['bytes']
        row = {'layout':sample['layout'], 'scenario':sample['scenario'],
               'prepare_ms':f['prepare_s']*1000, 'execute_ms':f['execute_wall_s']*1000,
               'data_fill_ms':fill*1000, 'verify_publish_ms':f['verify_and_publish_s']*1000,
               'publication_ms':f['coordinator_wall_s']*1000,
               'unattributed_ms':(f['coordinator_wall_s']-fill-f['verify_and_publish_s'])*1000,
               'hbm_source_bytes':hbm, 'dram_source_bytes':536870946-hbm, 'actual_kind_bytes':dict(kinds)}
        result['layouts'].append(row)
        values = [row[k] for k in ('prepare_ms','execute_ms','data_fill_ms','verify_publish_ms','publication_ms')]
        lines.append(f'| {sample["layout"]} | {sample["scenario"]} | '+
                     ' | '.join(f'{v:.3f}' for v in values)+f' | {hbm/(1<<20):.6f} | {(536870946-hbm)/(1<<20):.6f} |')
    # Read-only checks of the protected workers and experiment container cleanup.
    current = []
    for gpu in range(2, 8):
        with urllib.request.urlopen(f'http://12.11.3.28:2970{gpu}/inventory?compact=1', timeout=5) as response:
            inventory = json.load(response)
        assert not inventory['transactions']
        current.append({'gpu':gpu,'epoch':inventory['epoch'],'session':inventory['session'],
                        'replicas':[r['id'] for r in inventory['replicas'] if r['state']=='VALID'],
                        'used_bytes':inventory['used_bytes']})
    result['protected_inventory_unchanged'] = current == data['protected_before']
    assert result['protected_inventory_unchanged']
    command = ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5','node28',
               'docker ps -a --filter name=zjn-fluidckpt-task2-e3-e3_c01_layouts_r --format "{{.Names}} {{.Status}}"']
    check = subprocess.run(command, check=True, capture_output=True, text=True)
    assert not check.stdout.strip(), check.stdout
    result['cleanup_check'] = {'command':command,'stdout':check.stdout,'stderr':check.stderr,'exit_code':check.returncode}
    result['missing_fields'] = ['e3 full-model weights_ready and generation: not measured',
        'continuous allocator/CPU/foreground peak samples: not measured',
        'complete historical runtime binary identity: not certified by this reanalysis',
        'independent runtime version capture for e3: not measured; topology and process env retained']
    (out/'evidence.json').write_text(json.dumps(result, indent=2))
    (out/'tables.md').write_text('\n'.join(lines)+'\n')
    files = []
    for run in runs:
        files.extend(p for p in run.rglob('*') if p.is_file())
        files.extend(p for p in run.parent.glob(run.name+'.*') if p.is_file())
    files.extend(p for p in (BASE/'protocols').glob('*.json'))
    files.extend([Path(__file__),out/'evidence.json',out/'tables.md'])
    manifest = {str(p.relative_to(ROOT)):{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                 'size':p.stat().st_size} for p in sorted(set(files))}
    (out/'artifact_manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps({'status':'passed','historical_samples':len(result['historical']),
                      'e3_observations':len(result['layouts']), 'artifacts':len(manifest),
                      'protected_inventory_unchanged':True,'own_containers_removed':True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    main(parser.parse_args().out)
