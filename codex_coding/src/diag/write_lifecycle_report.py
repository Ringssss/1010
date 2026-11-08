"""Write a concise Chinese report from recorded lifecycle measurements."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    analysis = json.loads((args.run_dir/'analysis.json').read_text())
    data = json.loads((args.run_dir/'summary.json').read_text())
    if not analysis['passed']: raise RuntimeError('run has not passed')
    rel = os.path.relpath(args.run_dir, args.out.parent)
    ssd_node = data['ssd_backend']['node']
    nodes = {'node27', 'node28', 'node29', ssd_node}
    run_date = datetime.fromtimestamp(data['events'][0]['time']).strftime('%Y-%m-%d')
    reserve_gib = analysis['service_reserve_bytes_per_gpu']/2**30
    lines = [f'# FluidCKPT {len(nodes)}节点双模型生命周期实测', '',
        f'日期：{run_date}；run：`{args.run_dir.name}`；状态：passed。', '',
        '已完成真实 A→B→远端A→本地A及重复切换。A为Qwen3.6-35B-A3B，B为9g_8b_thinking_llama；均TP2、eager推理。', '',
        f'node27提供本地服务，node28提供DRAM保活，node29运行远端A，{ssd_node}提供CPU/本地NVMe文件服务。每个本地服务GPU实际分配{reserve_gib:g}GiB压力预留，模拟其他业务/KV所需空间；真实KV与运行时另计。', '',
        '基线是用户认可的HIP IPC机制适配，以及HBM逐出后在同一已初始化引擎中执行原生磁盘loader的回载路径。不是厂商镜像原生Weight Cache Daemon测评。双方复用warm engine，避免把引擎重启成本只算给基线。', '',
        '## 端到端观察', '',
        '| 场景 | 逐出后磁盘回载s | FluidCKPT s | 观察到的比值 |',
        '| --- | ---: | ---: | ---: |']
    labels = ['A→B（含释放）', 'B→A（含释放）', '远端请求A']
    for label, row in zip(labels, analysis['paired_observations']):
        lines.append(f'| {label} | {row["baseline_s"]:.3f} | {row["candidate_s"]:.3f} | {row["ratio"]:.2f}× |')
    ipc = next(r for r in data['transitions'] if r['phase'].startswith('baseline_ipc_hit'))
    initial_disk = next(r for r in data['transitions'] if r['phase'] == 'baseline_A_disk')
    lines += ['', f'表中终点都是首个正确token。纯resident HIP IPC命中为{ipc["first_correct_token_s"]:.3f}s，仍快于完整的远端恢复。上述比值只描述这条容量受限轨迹。', '',
              f'单独的首次A磁盘回载为{initial_disk["first_correct_token_s"]:.3f}s；表内采用随后的对应切换观测。page cache采用系统自然状态，首次/重复路径差异尚未单独归因。', '',
              f'![端到端对照]({rel}/paired.png)', '', f'![物理HBM轨迹]({rel}/lifecycle.png)', '',
              '## 权重实际位置与校验', '']
    placement = analysis['placement']
    for location, n in placement['bytes_by_location'].items():
        lines.append(f'- `{location}`：{n:,} bytes（{n/2**30:.4f} GiB）。')
    lines += ['', 'A停放后，本地没有其保活权重HBM或DRAM副本；warm engine仍持有运行时/KV等开销，因此不宣称整卡显存归零。',
              f'迁移完整校验了{placement["total_weight_bytes"]:,} bytes；远端恢复再次进行完整SHA256校验。所有恢复生成均匹配原始输出。', '',
              f'MILP求解{placement["solve_s"]:.4f}s，gap={placement["mip_gap"]:.4g}；最优性只限给定块、候选和近似服务模型。恢复计划使用资源约束LP。', '']
    migration = analysis['migration']
    lines += ['## 必须单独计入的成本', '',
        f'一次分布迁移共{migration["coordinator_wall_s"]:.3f}s，其中分配/准备{migration["prepare_s"]:.3f}s、执行传输{migration["execute_wall_s"]:.3f}s、完整校验及发布{migration["verify_and_publish_s"]:.3f}s。这些工作在B服务期间执行，不包含在随后A的恢复延迟里。', '',
        '首次模型加载、注册保活内存、引擎初始化和初次预热也没有消失，只支付在初始化阶段。当前结果不能代表首次请求或迁移未完成时的紧急召回。', '']
    for event in data['events']:
        if event['event'] == 'shell_ready':
            lines.append(f'- `{event["label"]}` 一次性启动、捕获/恢复、预热与参考生成：{event["startup_s"]:.2f}s。')
    lines += ['', '显式副本容量与物理HBM有记录；迁移时旧/新副本暂时共存。当前未用统一硬额度封顶所有进程RSS和内核page cache，后续严格同预算评估需补齐这部分账本。', '']
    b = analysis['B_samples']
    lines += ['## B的服务表现', '',
        f'空闲对照{b["idle_count"]}次请求，迁移/恢复期间{b["during_movement_count"]}次；全部输出一致，无请求错误。',
        f'TTFT中位数：{b["idle"]["median_ttft_s"]*1000:.2f}→{b["during_movement"]["median_ttft_s"]*1000:.2f}ms；期间最大观察值{b["during_movement"]["max_ttft_s"]*1000:.2f}ms。样本不足以报告P99保证。', '']
    ssd = analysis['ssd']
    lines += ['## SSD与page cache', '',
        f'{ssd_node}本地NVMe目录真实写入并提交{ssd["written_bytes"]/2**30:.2f}GiB；文件服务应用层累计读取{ssd["read_bytes"]/2**30:.2f}GiB。策略是buffered I/O、fsync及POSIX_FADV_DONTNEED；该读取计数不是NVMe物理读计数。未修改Linux内核。', '',
        '当前SSD经CPU HTTP代理供给GPU，性能包含文件读取、网络、临时内存和H2D等开销。不能把整条路径的耗时归因于SSD硬件。', '',
        '## 结论边界与下一步', '',
        '这是一条真实、固定配置的功能与性能轨迹，尚未完成随机化成对重复、更多模型/上下文、原生CPU backup或多层缓存的强基线，以及MILP相对简单策略的消融。单独观察到端到端收益不证明MILP的增量收益。', '',
        '下一步优先比较同量HBM保留慢路径区域与均分/热度策略；拆分SSD代理的读取、固定内存和H2D开销；让迁移可分段提交并接受中途召回；严格计入迁移峰值和一次性准备成本。', '',
        f'执行代码指纹变化：`{analysis["changed_source_files_since_start"]}`。清理错误：`{analysis["cleanup_errors"]}`。', '',
        f'原始数据：[summary.json]({rel}/summary.json)、[analysis.json]({rel}/analysis.json)、[会话与命令]({rel}/session.json)。', '',
        '## 复现命令', '', '在项目根目录运行；使用新的run名称：', '', '```bash',
        f'python3 codex_coding/src/task2/cluster/run_lifecycle.py --run <new_run> --local-dram-gib 0 --port-base 29100 --ssd-node {ssd_node}',
        'python3 codex_coding/src/task2/cluster/analyze_lifecycle.py --run-dir codex_coding/results/lifecycle/<new_run>',
        '# Plot with a Python environment containing matplotlib and numpy:',
        'python codex_coding/src/plt/plot_lifecycle.py --run-dir codex_coding/results/lifecycle/<new_run>', '```', '']
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text('\n'.join(lines))
    print(args.out)


if __name__ == '__main__': main()
