"""Audit frozen sources and policy scope, and run independent CPU comparisons."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT/'main_lib'))
from fluidckpt.benchmarks.baseline_scope import (
    BLITZ_CACHE, BLITZ_TRANSFER, SOURCE_FILES, SLLM_POLICY,
    algorithm_adaptations, baseline_scopes)


def source_references(path):
    source = (ROOT/path).read_text()
    if path.endswith('.py'):
        names = {'_get_model_loading_time', '_get_migration_target', '_get_scheduling_options',
                 'load_model', '_fallback_load', 'get_store_info', 'load_to_host', '_lru_eviction'}
        return [dict(path=path, line=node.lineno, symbol=node.name)
                for node in ast.walk(ast.parse(source))
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    if path == BLITZ_CACHE:
        names = ('machine_has_cache', 'find_one_rank_within_cache', 'assign_nvlink_chains', 'assign_tanz_chains')
    elif path == BLITZ_TRANSFER:
        names = ('max_lane_num', 'rdma_broadcast')
    else:
        names = ('bool ModelInstanceMgr::send_model_wakeup(', 'bool ModelInstanceMgr::send_model_wakeup_d2d(')
    references = []
    for number, line in enumerate(source.splitlines(), 1):
        for symbol in names:
            if symbol in line and (symbol not in {row['symbol'] for row in references}):
                references.append(dict(path=path, line=number, symbol=symbol))
    return references


def audit(out):
    out.mkdir(parents=True, exist_ok=False)
    lock = json.loads((ROOT/'lib_cite/baselines.lock.json').read_text())
    manifest = []
    for repo in lock['repositories']:
        head = subprocess.check_output(['git', '-C', str(ROOT/repo['path']), 'rev-parse', 'HEAD'], text=True).strip()
        status = subprocess.check_output(['git', '-C', str(ROOT/repo['path']), 'status', '--short'], text=True).strip()
        files = []
        for name in SOURCE_FILES[repo['name']]:
            content = (ROOT/name).read_bytes()
            files.append(dict(path=name, sha256=hashlib.sha256(content).hexdigest(), bytes=len(content)))
        manifest.append(dict(name=repo['name'], upstream_commit=repo['commit'], observed_commit=head,
                             head_matches_lock=head == repo['commit'], worktree_clean=not status,
                             source_url=repo['source_url'], source_files=files))
    validation = subprocess.run([sys.executable, str(Path(__file__).with_name('test_baseline_scope.py'))],
                                cwd=ROOT, text=True, capture_output=True)
    (out/'cpu_validation.log').write_text(validation.stdout+validation.stderr)
    references = [ref for paths in SOURCE_FILES.values() for path in paths for ref in source_references(path)]
    rows = baseline_scopes()
    for row in rows:
        row['source_references'] = [ref for ref in references if ref['path'] in row['source_files']]
    adaptations = algorithm_adaptations()
    for adaptation in adaptations:
        adaptation['source_references'] = [ref for ref in references if ref['path'] in adaptation['source_files']]
        repo = next(repo for repo in lock['repositories']
                    if any(path.startswith(repo['path']+'/') for path in adaptation['source_files']))
        adaptation.update(upstream_commit=repo['commit'], source_url=repo['source_url'])
    passed = validation.returncode == 0 and all(row['head_matches_lock'] and row['worktree_clean'] for row in manifest)
    summary = dict(schema_version=1, development_stage='v0.1.23.3/task2',
                   run_kind='cpu_source_scope_audit', status='passed' if passed else 'failed',
                   new_gpu_measurements=0, baseline_scopes=rows, algorithm_adaptations=adaptations,
                   source_manifest=manifest, validation=dict(command=[sys.executable,
                       str(Path(__file__).with_name('test_baseline_scope.py'))], returncode=validation.returncode,
                       log_path=str(out/'cpu_validation.log'), oracle='AST extracted actual frozen upstream methods'),
                   status_semantics=dict(applicable='已实现机制能表达该场景，仍需现场准入；非本次GPU测量',
                       conditional='合法来源/位置/证据或冷加载驱动条件待验收',
                       not_adapted='系统或transfer策略未形成可执行适配，保留源码能力及缺口',
                       infeasible='该具体机制在场景合法来源/容量下不可行，非完整系统无后备'))
    (out/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    report = ['# Baseline源码能力与最小方法适配', '',
        '开发编号v0.1.23.3/task2。本轮只做源码核查和CPU一致性验证，新增GPU性能样本0。', '',
        'ServerlessLLM忠实复用整模型pinned命中/磁盘等待估价与迁移候选排序，标记decision_only_not_runtime。'
        '输入model_size为bytes、带宽bytes/s、waiting与输出为秒；需上游store与硬件状态。'
        '原版miss分支为waiting+model_size/disk，不加入PCIe项；hit分支为model_size/pcie。'
        'TP2模型必须在节点整模型语义中合计两rank字节；本子模块不制造任何逐rank传输计划。', '',
        '固定目标只有一个合法候选时可复用估价，但没有位置决策增量。未实现原版多层加载后端、迁移、缓存驱逐与运行时调度。', '',
        'S1只保证目标HBM完整，不排除host/active后备；Janus式copy是否适用需核查实际来源，直接IPC不属于此机制。'
        'S2的Janus式D2D方法明确source_mode=host_fallback，此行不是D2D性能测量。', '',
        'BlitzScale源码已有机器缓存/TTL、TP与NVLink/RDMA广播能力。缓存metadata命中不证明物理完整来源；'
        'HashSet首源没有稳定tie-break。当前TP2两rank内容不同，不直接构成同内容broadcast。'
        '当前单目标串行copy缺广播/relay状态、源组映射和逐层服务接口，故不硬造分发适配。'
        '单目标walzer_Tanz路径max_lane_num受目标逻辑实例数限制，不提供多接收者扩容优势。', '',
        'S4完整DRAM后备仍合法，8GiB不能保住65.5GiB完整HBM副本；该结论只限制完整HBM/IPC机制。'
        'Janus式warm要求目标本地完整host，不等于原版Janus没有其他恢复路径。'
        'S5本轮软件worker串行观察不推断物理fabric共享。', '',
        '| 方法 | 身份 | S1 | S2 | S3 | S4 | S5 |', '| --- | --- | --- | --- | --- | --- | --- |']
    for method_id in dict.fromkeys(row['method_id'] for row in rows):
        group = [row for row in rows if row['method_id'] == method_id]
        report.append('| '+method_id+' | '+group[0]['identity']+' | '+' | '.join(row['applicability'] for row in group)+' |')
    report += ['', '各单元具体合法来源、后备、缺口、准确commit、源码行号与历史证据路径见summary.json；'
               'applicable是机制表达能力，原版/策略未适配和历史GPU证据各自保留。', '',
               f'CPU校验：{"通过" if validation.returncode == 0 else "失败"}；冻结源码与工作区：'
               f'{"通过" if all(row["head_matches_lock"] and row["worktree_clean"] for row in manifest) else "失败"}。', '',
               '停止条件：固定目标及同一完整host来源不能证明ServerlessLLM选址增量；不继续以nearest/fastest替名。'
               'BlitzScale若要比较分发，先限定完整TP源组与可执行transfer后端；否则保持not_adapted。']
    (out/'report.md').write_text('\n'.join(report)+'\n')
    print(json.dumps(dict(status=summary['status'], out=str(out), rows=len(rows)), ensure_ascii=False))
    return 0 if passed else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(audit(args.out))
