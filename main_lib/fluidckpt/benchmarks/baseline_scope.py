"""Frozen-source baseline scope and CPU-only policy subsets.

These subsets do not construct an executable restore plan. Cache metadata and
estimated latency must never be treated as proof of physical weight coverage.
"""
import math
from pathlib import Path

from fluidckpt.benchmarks.basic_scenarios import GIB, method_catalog, scenario_contract

ROOT = Path(__file__).resolve().parents[3]
SLLM_POLICY = 'lib_cite/ServerlessLLM/sllm/schedulers/storage_aware_scheduler.py'
BLITZ_CACHE = 'lib_cite/blitz-scale/router_v2/src/replica/steersman/mod.rs'
BLITZ_TRANSFER = 'lib_cite/blitz-scale/router_v2/src/replica/cybernetics/exec_blitz.rs'
SOURCE_FILES = {
    'SGLang': ['lib_cite/sglang/python/sglang/srt/weight_cache/ipc_loader.py',
               'lib_cite/sglang/python/sglang/srt/weight_cache/daemon.py'],
    'Janus': ['lib_cite/Janus/janus_service/janus_service/scheduler/managers/model_instance_mgr.cpp'],
    'BlitzScale': [BLITZ_CACHE, BLITZ_TRANSFER],
    'ServerlessLLM': [SLLM_POLICY, 'lib_cite/ServerlessLLM/sllm/store_manager.py'],
}


def _nonnegative(value, label):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f'{label} must be finite and nonnegative')
    return value


def serverlessllm_loading_time(model_name, model_size, hardware_info,
                              node_waiting_time, pinned_memory_pool):
    """Preserve upstream whole-model hit/miss estimate, including its branches.

    Upstream miss estimates waiting + disk and does not add the PCIe term.
    This estimate is not a claim about measured end-to-end restoration time.
    """
    size = _nonnegative(model_size, 'model_size')
    waiting = _nonnegative(node_waiting_time, 'node_waiting_time')
    key = 'pcie_bandwidth' if model_name in pinned_memory_pool else 'disk_bandwidth'
    bandwidth = _nonnegative(hardware_info[key], key)
    if bandwidth == 0:
        raise ValueError(f'{key} must be positive')
    return size / bandwidth if model_name in pinned_memory_pool else waiting + size / bandwidth


def serverlessllm_migration_target(model_name, num_gpu, source_node_id,
                                 worker_nodes, model_info, store_info, hardware_info):
    """Preserve upstream eligible-node filter and stable latency ordering.

    Store metadata layout is [disk_models, pinned_model_map, waiting_seconds].
    Inputs are whole-model node state, not independent TP shard cache rows.
    """
    if type(num_gpu) is not int or num_gpu <= 0:
        raise ValueError('num_gpu must be a positive integer')
    candidates = []
    for node_id, info in worker_nodes.items():
        if node_id == source_node_id:
            continue
        if info['free_gpu'] >= num_gpu:
            seconds = serverlessllm_loading_time(
                model_name, model_info[model_name], hardware_info[node_id],
                store_info[node_id][2], store_info[node_id][1])
            candidates.append((node_id, seconds))
    candidates.sort(key=lambda row: row[1])
    return candidates[0] if candidates else None


def algorithm_adaptations():
    return [
        dict(algorithm_id='serverlessllm_whole_model_loading_and_target',
             implementation='implemented_cpu_subset', identity='policy_subset', source_files=[SLLM_POLICY],
             upstream_symbols=['StorageAwareScheduler._get_model_loading_time',
                               'StorageAwareScheduler._get_migration_target'],
             scope='整模型pinned命中/磁盘等待估价与迁移候选节点排序；保留原版分支与稳定排序',
             fixed_target_behavior='单合法目标时仅输出该目标与估价，不产生独立放置增量',
             scope_level='decision_only_not_runtime', units=dict(model_size='bytes', bandwidth='bytes/s', latency='s'),
             out_of_scope_conditions=['任意HBM部分块、多源跨rank取数不属于此整模型估价函数',
                                      '候选节点不可选时不比较位置感知调度增量'],
             executable_restore_plan=False, measurement_status='not_measured',
             minimal_gap=['真实整模型store/硬件带宽状态映射', '当前处理后TP2快照的加载后端与逐rank覆盖准入',
                          '若比较位置选择，需允许多个合法目标；固定目标不能冒充位置感知调度收益']),
        dict(algorithm_id='blitzscale_cache_and_tp_distribution',
             implementation='not_adapted', identity='source_audit', source_files=[BLITZ_CACHE, BLITZ_TRANSFER],
             upstream_symbols=['Steersman::machine_has_cache', 'Steersman::find_one_rank_within_cache'],
             scope='源码具备机器缓存TTL/已加载rank、NVLink semaphore准入及完整TP组分发；本轮只审查',
             scope_level='source_audit_only',
             out_of_scope_conditions=['单目标串行copy不提供多接收者broadcast/relay调度接口',
                                      '没有真实NVLink/RDMA及源端保护证据时不声称已复现分发',
                                      '以全部权重ready衡量时不评价逐层提前服务的原版收益'],
             fixed_target_behavior='准入元数据不提供跨机器transfer plan，也不能将不同TP内容rank组成单内容广播',
             executable_restore_plan=False, measurement_status='not_measured',
             minimal_gap=['完整TP逻辑实例与机器缓存状态映射', 'NVLink/RDMA等价传输及源/目标状态与semaphore保护',
                          '现执行器不支持upstream广播/relay/逐层提前服务；需单独限定分发子机制验收']),
    ]


def baseline_scopes(model_bytes=70329718112, extra_hbm_bytes=8*GIB, lock_path=None):
    """Return a declarative capability table, separate from live-case admission.

    Applicable means an existing mechanism can express the scenario after its
    runtime gates pass. Historical observations are identified separately.
    """
    _nonnegative(model_bytes, 'model_bytes')
    _nonnegative(extra_hbm_bytes, 'extra_hbm_bytes')
    methods = method_catalog(lock_path)
    rows = []
    for method_id, method in methods.items():
        for scenario_id in ('S1', 'S2', 'S3', 'S4', 'S5'):
            upstream = method.get('upstream', {})
            row = dict(method_id=method_id, identity=method['identity'], scenario_id=scenario_id,
                       scenario=scenario_contract(scenario_id)['name'],
                       applicability='conditional', implementation=method['implementation'],
                       measurement_status='not_measured', historical_evidence=[],
                       source_files=SOURCE_FILES.get(upstream.get('name'),
                           ['main_lib/fluidckpt/cluster/planning.py']),
                       source_url=upstream.get('source_url'), upstream_commit=upstream.get('commit'),
                       minimal_gap=[], reason='', decision_gain='not_established')
            if method['identity'] == 'original_system':
                row.update(implementation='not_adapted', applicability='not_adapted',
                           reason='冻结官方源码已取得，原版系统尚未安装/复现；能力可研究但无本项目性能验收',
                           minimal_gap=['平台与模型/TP2布局准入', '原版端到端正确性、计时与资源账本验收'])
                if method_id == 'sglang_original':
                    row.update(applicability='conditional',
                               reason='原生checkpoint冷加载可作为各场景存储后备；尚未做本轮原版baseline测量',
                               minimal_gap=['同模型TP与格式建立成本对齐', '原生冷加载驱动、page-cache状态与计时协议'])
            elif method_id in ('blitzscale_distribution', 'serverlessllm_locality'):
                row.update(applicability='not_adapted', implementation='not_adapted')
                index = 1 if method_id == 'blitzscale_distribution' else 0
                adaptation = algorithm_adaptations()[index]
                row.update(minimal_gap=adaptation['minimal_gap'],
                           reason=adaptation['scope']+'；尚未形成此场景可执行恢复方法',
                           scope_level=adaptation['scope_level'])
            elif method_id == 'sglang_ipc_hip':
                if scenario_id == 'S1':
                    row.update(applicability='applicable', reason='完整兼容目标HBM及活owner允许专用HIP IPC接管',
                               historical_evidence=['codex_coding/results/v0.1.23.3/task2/paper_e1_e8/e1'])
                elif scenario_id == 'S5':
                    row.update(reason='只有存在完整兼容目标HBM时IPC可用；本轮S5不据软件串行证据推断物理fabric')
                else:
                    row.update(applicability='infeasible', reason='该场景没有完整目标HBM；IPC机制不能由host/active/部分块补齐')
                    if scenario_id == 'S4' and extra_hbm_bytes < model_bytes:
                        row['reason'] = f'额外HBM预算{extra_hbm_bytes}字节小于完整模型{model_bytes}字节；完整HBM保活不可行，host后备仍合法'
            elif method_id in ('janus_host', 'janus_d2d'):
                if scenario_id == 'S1':
                    prerequisite = ('目标本地完整host' if method_id == 'janus_host' else
                                    '受保护完整active源，或目标本地完整host后备')
                    row.update(applicability='conditional',
                               reason='S1只保证完整目标HBM，不排除同时存在host/active后备；此copy机制依赖'+prerequisite+'，直接IPC不属于此机制',
                               minimal_gap=['确认实际'+prerequisite+'及TP覆盖；不能从S1未保证该来源推断其不存在'])
                elif scenario_id == 'S2':
                    row.update(applicability='applicable', reason='目标主机完整host保留可恢复；D2D机制在无active时使用host后备')
                    if method_id == 'janus_host':
                        row['historical_evidence'] = ['codex_coding/results/v0.1.23.3/task2/basic_scenarios/s2_r01']
                    else:
                        row.update(source_mode='host_fallback',
                                   reason='S2无active源，Janus式D2D方法只能使用目标本地完整host后备；此行不是D2D性能测量')
                elif scenario_id == 'S3' and method_id == 'janus_d2d':
                    row.update(applicability='applicable', reason='受复制保护的完整单逻辑active实例按TP对应复制；无源时允许目标本地host后备',
                               historical_evidence=['codex_coding/results/v0.1.23.3/task2/basic_scenarios/s3_r01'])
                elif scenario_id == 'S3':
                    row.update(reason='需目标主机完整host底本；只有远端host且无本地warm cache时此机制不可行')
                elif scenario_id == 'S4':
                    row.update(reason='完整目标本地host可作为后备；8GiB部分HBM不等于Janus完整warm/D2D源',
                               minimal_gap=['核查完整host底本实际位置；远端底本不冒充Janus本地warm'])
                else:
                    row.update(reason='先证明合法本地host或完整受保护active源及实际共享瓶颈；软件worker串行不证明物理fabric')
            else:
                row.update(applicability='applicable' if scenario_id != 'S5' else 'conditional',
                           reason='共享相同合法来源、预算与IPC机会；运行前仍须过内容覆盖、profile与实际执行器准入')
                if scenario_id == 'S5':
                    row.update(reason='只可对已测worker串行资源提出结论；物理fabric共享未测，需对应证据才能扩大范围',
                               minimal_gap=['共享资源归属、并发与调度等待证据', '规划资源与真实执行语义一致性'])
                if method_id == 'resource_lp' and scenario_id in ('S2', 'S3'):
                    row['historical_evidence'] = [f'codex_coding/results/v0.1.23.3/task2/basic_scenarios/{scenario_id.lower()}_r01']
            rows.append(row)
    return rows
