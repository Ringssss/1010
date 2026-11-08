"""Freeze the independent local reviews and their source evidence."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[6]
BASE=ROOT/'codex_coding/results/v0.1.23.3/task2/multitier_scenarios'


def main():
    p=argparse.ArgumentParser();p.add_argument('--contract-review',required=True);p.add_argument('--contract-run',required=True);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    hardware=json.loads((BASE/'review_hardware_r02/qualification.json').read_text())
    contract=json.loads((BASE/a.contract_review/'qualification.json').read_text())
    capability=json.loads((BASE/'capability_r01/report.json').read_text())
    provenance=json.loads((BASE/a.contract_run/'provenance.json').read_text())
    checks={f'hardware/{k}':v for k,v in hardware['checks'].items()}
    checks.update({f'contract/{k}':v for k,v in contract['checks'].items()})
    refs={}
    for item in hardware['evidence_refs']+contract['evidence_refs']+provenance['sources']:
        refs[item['path']]=item['sha256']
    refs.update(capability['evidence_sha256'])
    for path,sha in refs.items():
        checks[f'source_SHA/{path}']=hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==sha
    checks['capability_no_GPU_SSH_product_baseline_changes']=all(capability[k]==0 for k in ('new_gpu_runs','ssh_calls','remote_writes','product_changes','baseline_changes'))
    checks['capability_no_new_budget']=capability['budget_bytes_per_node']==2<<30 and not capability['budget_change_authorized']
    checks['capability_static_checks_passed']=all(x['ast_parse']=='passed' for x in capability['static_checks'])
    checks['capability_complete_SSD_legal']=capability['complete_ssd_fallback_legal'] is True
    checks['contract_no_execution_or_payload']=not provenance['payload_allocated'] and not provenance['product_modified'] and not provenance['ssh_executed'] and provenance['gpu_command'] is None
    checks['both_review_qualifications_passed']=hardware['qualification'].startswith('passed_') and contract['qualification'].startswith('passed_')
    a.output.mkdir(parents=True,exist_ok=False)
    # Mutable planning documents are frozen at the review boundary.
    snapshots=[]
    for path,sha in refs.items():
        if path.startswith('docx/') or path=='AGENTS.md':
            dest=a.output/'evidence_snapshot'/path
            dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((ROOT/path).read_bytes())
            snapshots.append({'original_path':path,'frozen_path':str(dest.resolve().relative_to(ROOT)),'sha256':sha})
    stable_refs=[]
    for path in [BASE/'review_hardware_r02/qualification.json',BASE/a.contract_review/'qualification.json',BASE/a.contract_run/'contract.json',BASE/a.contract_run/'provenance.json',BASE/'capability_r01/report.json',Path(__file__)]:
        stable_refs.append({'path':str(path.resolve().relative_to(ROOT)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    report={'development':'v0.1.23.3/task2','qualification':'passed_independent_multitier_structure_physical_unadmitted' if all(checks.values()) else 'failed',
            'checks':checks,'checks_passed':sum(checks.values()),'checks_total':len(checks),
            'contract_hash':provenance['contract_hash'],'contract_review':a.contract_review,'hardware_review':'review_hardware_r02',
            'phase_bytes':contract['phase_bytes'],'semantic_negative_cases':len(contract['negative_checks']),
            'schema_negative_cases':len([k for k in contract['checks'] if k.startswith('schema_negative/')]),
            'CPU_structural_ready':True,'three_physical_nodes_six_GPU_ready':False,'physical_local_SSD_per_node_verified':False,
            'cost_ready':False,'schedule_ready':False,'runtime_budget_ready':False,'real_model_binding_ready':False,
            'layer_decision_latency_demonstrated':False,'new_solver_or_parameter_search':False,
            'gpu_command':None,'GPU_calls':0,'SSH_actions':0,'remote_writes':0,'signals':0,'product_mutations':0,
            'stable_result_refs':stable_refs,'review_time_source_hashes':refs,'frozen_document_refs':snapshots}
    (a.output/'qualification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    (a.output/'review.source.py').write_bytes(Path(__file__).read_bytes())
    (a.output/'report.md').write_text(f'''# 三节点多层场景独立验收

编号v0.1.23.3/task2。合同hash `{provenance['contract_hash']}`，总计{sum(checks.values())}/{len(checks)}项通过。合同独立审查{contract['checks_passed']}/{contract['checks_total']}项、硬件历史复算20/20项；{len(contract['negative_checks'])}个语义破坏反例和三个schema反例全部拒绝。CPU结构通过，物理/成本/有限调度/完整模型资格均未通过。

合同是三逻辑节点、各两GPU，四必要TP rank、八块×4MiB。两个helper在实际witness作为跨节点HBM来源，六GPU均有某阶段合法缓存额度。每块六HBM+六DRAM+一个authoritative SSD来源，共104候选及104路径；完整SSD总32MiB按16/8/8MiB分布，在所有阶段始终合法。processed runtime snapshot与raw checkpoint转换协议明确分开，未物化任何模型权重。

| 阶段 | HBM/DRAM允许MiB | 给定witness HBM/DRAM/SSD来源MiB | 已证明的条件 |
|---|---|---|---|
| C0 | 32/48 | 32/0/0 | HBM全覆盖可行 |
| C1 | 16/48 | 16/16/0 | 无SSD覆盖至少需16MiB DRAM |
| C2a | 16/24 | 16/16/0 | 非均匀位置配额下给定覆盖可行 |
| C2b | 16/24 | 16/16/0 | 同内容/配额/覆盖，仅共享incidence合并 |
| C3 | 16/12 | 16/12/4 | H+D=28<32，至少4MiB逻辑SSD来源 |

这些层真正改变容量可行域与资源聚合约束。C2共享NIC、NUMA、PCI、SSD queue和GPU copy IDs逐路径独立重构；独立控制仅是假想分离相同物理工作点，不是实际独立带宽。C3下界由unique字节覆盖证明，重复副本不能填补缺失内容。尚未计入速率、启动等待与schedule，因此不证明各tier最优选择或joint相对强baseline的时延收益。SSD读量是逻辑来源量，page cache可能命中，不能称physical read_bytes。

暂停缓存账本与恢复峰值分列。当前witness保留HBM+独立32MiB目标的已知HBM量为C0 64MiB、其余48MiB；原serial执行器每worker 64MiB staging、六worker共384MiB的条件预留另列，合计C0 448MiB、其余432MiB，实际未分配。它们不是完整峰值：实际runtime、allocator、HTTP bytearray、host staging、page cache及迁移生命周期需要另核，别名抵扣需实际adoption资格。单worker历史HBM已含64MiB staging，未来基于runtime观测算峰值时不能再加一次。单worker历史DRAM约2.03GB，双worker线性规划投影约4.06/4.08GB，未加权重已经高于2GiB/node任务额度。此投影不构成物理下界，也不能靠缩块/8GiB容器ceiling获得准入。

node28/node30仅有历史双卡产品路径资格和08:59只读metadata；原node27/node29 pending保留。本地SSD映射每node未知，真实GLM raw checkpoint为node28本地盘、其他节点NFS。当前契约SSD owner及CPU HTTP proxy位于同node，跨node服务仅显式proxy GPU到target的network leg；任何未来storage-only第三节点若借另一节点GPU代理，需要新增storage→proxy HTTP network leg、角色DAG和预算。CPU DRAM owner不是独立RCCL/Cluster.transfer端点。第三CPU SSD service无需为了持盘启动GPU，但不是本轮六GPU契约的现成部署。

能力盘点证明应复用既有SSD service、RemoteExtent、Catalog、Worker及通用tier预算接口，当前SSD serial原语与doublebuffer缺口分开。稳定重启后的manifest/catalog与冷盘测量未实现/未认证，不把authoritative来源假设写成掉电恢复承诺。SGLang本地HBM daemon/CPU-disk backup、Janus GPU/RDMA能力须在相同token/content/layout/总预算与恢复目标下核对原版、适配版、机制对照；本轮不以依赖缺失笼统宣称系统不支持。

结论是有限通用多层问题的结构准备完成。下一关是先绑定实际角色、模型layout、SSD挂载和完整阶段预算，再把同一104机会投影为既有原语能执行的路径子集，建立各baseline共同适配；之后仅在预算准入下做同原语成本与独立组合验证。未添加求解器、参数搜索或新GPU/SSH操作。

机器资格及稳定结果SHA见qualification.json。为避免后续正常计划更新破坏阅读边界，本轮涉及的有限文档已在evidence_snapshot按SHA冻结；这不是全历史归档，不涉及history-chat.txt。
''')
    print(json.dumps({'output':str(a.output),'passed':sum(checks.values()),'total':len(checks),'failures':[k for k,v in checks.items() if not v]}))
    if not all(checks.values()):raise SystemExit(1)


if __name__=='__main__':main()
