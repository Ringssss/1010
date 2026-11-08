"""Audit local historical hardware evidence without remote or GPU actions."""
import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[6]
RESULT = ROOT / 'codex_coding/results/v0.1.23.3/task2'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    checks, refs, observations = {}, [], {}

    def read(path):
        raw = path.read_bytes()
        refs.append({'path': str(path.relative_to(ROOT)), 'sha256': hashlib.sha256(raw).hexdigest()})
        return json.loads(raw)

    prior = read(RESULT / 'tp4_scenarios/feasibility_r03/deployment_feasibility.json')
    for node in ('node28', 'node30'):
        before = read(RESULT / f'layout_validation/operations_baseline_r01/baseline_{node}/stdout.json')
        after = read(RESULT / f'layout_validation/operations_control_r01/post_warm_{node}/stdout.json')
        dram = int(after['container_cgroup']['files']['memory.current']) - int(before['container_cgroup']['files']['memory.current'])
        hbm = int(after['selected_gpu_sysfs']['mem_info_vram_used']) - int(before['selected_gpu_sysfs']['mem_info_vram_used'])
        declared = prior['runtime_evidence']['single_worker_observations'][node]
        checks[f'{node}/runtime_dram_independently_reconstructed'] = dram == declared['fixed_runtime_dram_increment_bytes']
        checks[f'{node}/runtime_hbm_independently_reconstructed'] = hbm == declared['fixed_runtime_hbm_increment_bytes']
        checks[f'{node}/same_historical_container_and_boot'] = before['container']['id'] == after['container']['id'] and before['boot_id'] == after['boot_id']
        checks[f'{node}/two_worker_projection_exceeds_2GiB'] = 2 * dram > 2 << 30
        checks[f'{node}/one_worker_is_observation_not_full_peak'] = not declared['full_physical_peak_verified']
        checks[f'{node}/metadata_refresh_is_not_runtime_admission'] = not prior['metadata'][node]['fresh_gpu_runtime_qualified']
        observations[node] = {'single_worker_dram_increment_bytes': dram, 'single_worker_hbm_increment_bytes': hbm,
                              'two_worker_dram_projection_bytes': 2 * dram, 'task_node_dram_budget_bytes': 2 << 30,
                              'remaining_after_single_observed_runtime_bytes': (2 << 30) - dram,
                              'projection_is_physical_lower_bound': False, 'full_lifetime_peak_verified': False}
    checks['node27_pending_not_cleared'] = prior['pending_preserved']['node27_exit_verified'] is False
    checks['node29_pending_not_cleared'] = prior['pending_preserved']['node29_exit_verified'] is False
    checks['existing_keeper_not_borrowable'] = not prior['keeper_reuse']['admissible']
    checks['static_six_gpu_mapping_not_runtime_qualified'] = not prior['hardware']['other_GPU_current_functional_admission']
    checks['HCA_independence_unknown'] = not prior['hardware']['HCA_independence_measured']
    checks['historical_qualified_path_exact'] = prior['hardware']['latest_product_path'] == {
        'node28_GPU': 0, 'node30_GPU': 4, 'HCA': 'shca_0:1', 'network': 'IBext_v8', 'gdr': 0}
    checks['cgroup_ceiling_not_task_budget'] = all(prior['metadata'][n]['container_cgroup_memory_max_bytes'] == 8 << 30
                                                  and prior['metadata'][n]['task_dram_quota_bytes'] == 2 << 30 for n in observations)
    doc = ROOT / 'docx/plans/2026-10-03_three_node_glm_followup.md'
    text = doc.read_text()
    refs.append({'path': str(doc.relative_to(ROOT)), 'sha256': hashlib.sha256(doc.read_bytes()).hexdigest()})
    checks['checkpoint_storage_is_node28_local_other_nodes_NFS'] = '/data/node28/shared' in text and '其他节点为 NFS' in text
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    report = {'development': 'v0.1.23.3/task2', 'qualification': 'passed_local_historical_evidence_review_physical_unadmitted',
              'checks': checks, 'checks_passed': sum(checks.values()), 'checks_total': len(checks),
              'observations': observations, 'node27_runtime_projection': {'single_worker_dram_bytes': None,
                 'two_worker_dram_proxy_node30_bytes': 2 * observations['node30']['single_worker_dram_increment_bytes'],
                 'proxy_is_measurement_or_lower_bound': False, 'pending_collector_exit_verified': False},
              'logical_three_nodes_two_gpu_each_ready': False, 'physical_ssd_path_per_node_verified': False,
              'third_node_storage_only_option': {'deployment_ready': False, 'SSD_HTTP_service_needs_fresh_GPU_on_storage_node': False,
                 'CPU_DRAM_owner_is_standalone_Cluster_transfer_endpoint': False,
                 'requires_changed_rolemap_paths_lifetime_budget_and_functional_gate': True},
              'GPU_calls': 0, 'SSH_actions': 0, 'remote_writes': 0, 'signals': 0,
              'evidence_refs': refs, 'review_source': {'path': str(Path(__file__).resolve().relative_to(ROOT)),
                  'sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}}
    (output / 'qualification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    (output / 'report.md').write_text('''# 本地硬件可行性独立审查

三节点×两GPU是设计角色，不是已准入资源。node28/node30静态结构有历史证据；最新本地08:59元数据只确认容器/boot/保护身份，未证明新六worker、各GPU、SSD路径和共享容量当前可运行。node27旧D采集退出未证，node29旧pending保留，本轮不访问。

独立从warm前后原始cgroup/VRAM记录复算：node28新增DRAM 2,029,518,848B，node30 2,038,231,040B；单worker运行时约2GB。两worker线性投影分别4,059,037,696B和4,076,462,080B，应用字节未加已超过原2GiB/node额度。共享库页可降低净增量，mesh与校验可增量，因此这是规划投影而非物理下界；缩块不能消除固定运行时，也不能把8GiB容器ceiling当授权。node27无新观测，用node30代理须明确标为估计。

已通产品路径为node28 GPU0到node30 GPU4，经双方shca_0:1、IBext_v8、gdr0。按NUMA亲和配置其他HCA不等于路径独立或容量实测；六GPU新mesh、同node多源/多目标共享入口及并发资格均未知。两端历史warm HBM观测已包括64MiB staging，完整账本不得重复加这项；独立DRAM校验区、HTTP bytearray、page cache、pinned staging、allocator reserved和迁移暂存需按实际生命期计峰值。

原始GLM checkpoint在node28本地/data/node28/shared，其他节点通过NFS访问；不能把这个路径命名为各node的本地SSD。新契约的每node本地SSD是假设，节点路径、挂载、设备、可用容量及实际I/O仍待绑定。processed SSD snapshot须另建完整布局/内容身份，不以raw checkpoint字节替代，也不把逻辑SSD来源量当physical read_bytes。

第三节点可以只承接CPU SSD service，HTTP供给两个GPU节点上的接收/转发worker，无须为了SSD owner启动fresh GPU。但这是未来独立映射选项：修改六GPU角色、路径incidence、source-ready DAG、runtime预算与功能资格后才可执行。CPU DRAM owner本身不是独立Cluster.transfer端点；当前本地FD owner仍依赖同host GPU worker注册/发送。node27 pending和存储资格不会因采用CPU角色自动通过。

所有复算及SHA见qualification.json。未SSH、未GPU、未修改配额/进程/原XML/保护数据。
''')
    print(json.dumps({'output': str(output), 'passed': sum(checks.values()), 'total': len(checks)}))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
