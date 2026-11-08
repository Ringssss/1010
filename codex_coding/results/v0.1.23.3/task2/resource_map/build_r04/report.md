# node27/node29 资源地图建设

本地图builder只读本地冻结文件，无SSH/RPC/HIP调用；CPU验收不新增GPU实验。独立startup尝试作为证据引用。物理身份、runtime、可用量与历史观测分层。

node27：128逻辑CPU，DRAM 503.63087 GiB；采样 2026-10-08T04:00:02.358306+00:00。
node29：128逻辑CPU，DRAM 503.63087 GiB；采样 2026-10-08T04:00:02.841886+00:00。

GPU标称64GiB；hy-smi报告总量65520MiB，不能冒领整64GiB为可分配容量。Available与total-used均按原字段记录。
共享PCI对象仅一个稳定node/BDF身份，所有叶路径引用它；容量及互扰未知，不累加端口标称线速。

当前结构可读，性能准入为false：请求/预算未授权、外部owner活跃、历史微probe和RPC包络不充当当前速率。生成XML尚未获得实际runtime验收。

失败与未定：fresh snapshot随时间过期；无当前合法预算；反向网络、fabric内部、SSD和完整GPU组合速率未知。历史HIP ordinal映射独立标注原采样时间，fresh KFD/sysfs/BDF校验一致。

接口：resources/resource/shared_ancestors/path_evidence/validate_resource_map/planner_readiness/planning_input；无solver。

诊断请求（不执行）：四GPU0/1、builtin、node27→node29两条RCCL路径，预算全部null。具体判据：
- expired resource/owner sample node27/pci/0000:09:00.0
- incomplete owner inventory node27/pci/0000:09:00.0
- active external owners node27/pci/0000:09:00.0
- resource is nonexclusive node27/pci/0000:09:00.0
- missing or insufficient authorized budget node27/pci/0000:09:00.0
- expired resource/owner sample node27/pci/0000:36:00.0
- incomplete owner inventory node27/pci/0000:36:00.0
- active external owners node27/pci/0000:36:00.0
- resource is nonexclusive node27/pci/0000:36:00.0
- missing or insufficient authorized budget node27/pci/0000:36:00.0
- control reachability unconfirmed node29
- own probe cleanup unconfirmed node29
- expired resource/owner sample node29/pci/0000:09:00.0
- incomplete owner inventory node29/pci/0000:09:00.0
- active external owners node29/pci/0000:09:00.0
- resource is nonexclusive node29/pci/0000:09:00.0
- missing or insufficient authorized budget node29/pci/0000:09:00.0
- expired resource/owner sample node29/pci/0000:36:00.0
- incomplete owner inventory node29/pci/0000:36:00.0
- active external owners node29/pci/0000:36:00.0
- resource is nonexclusive node29/pci/0000:36:00.0
- missing or insufficient authorized budget node29/pci/0000:36:00.0
- multi-path joint resource admission unavailable; aggregate working points are not per-path capacities
- no current matching admitted rate {"dst": "node29/pci/0000:09:00.0", "dst_tier": "hbm", "src": "node27/pci/0000:09:00.0", "src_tier": "hbm"}
- no current matching admitted rate {"dst": "node29/pci/0000:36:00.0", "dst_tier": "hbm", "src": "node27/pci/0000:36:00.0", "src_tier": "hbm"}

窄规划接口暂仅准入单定向路径；多路径共享资源准入未建设，aggregate rate不会复制成各路径容量。拓扑/历史查询仍可返回全部路径。

复核命令：
```bash
python3 codex_coding/src/v0.1.23.3/task2/resource_map/build/verify_resource_map.py --map codex_coding/results/v0.1.23.3/task2/resource_map/build_r04/resource_map.json --out codex_coding/results/v0.1.23.3/task2/resource_map/build_r04/cpu_validation_new.json
```

当前速率必须精确匹配runtime_config_fingerprint与availability_context_id，不能只凭配置名称或旧负载快照准入。
复用已有discover序号/BDF映射、path_catalog证据引用与冻结profile引用；旧成本与solver保持原版本。

独立XML startup未取得actual RCCL证据：首轮只读准入snapshot超时；重试Gloo控制组仅2/4clients到达，未进入XML接受阶段。失败不证明XML格式错误，owned XML仍runtime_observed=false。完整结构和通信运行时分别验收。

最新控制与本任务probe清理状态独立保存于current_operational_status；未知/不可达不被12:00的离散availability快照替代，也不判定受保护keeper已损坏。
```json
{
  "scope": "control/own-probe cleanup evidence only; protected postflight is incomplete",
  "protected_state_corrupted": null,
  "protected_postflight_complete": false,
  "nodes": {
    "node27": {
      "control_reachable": true,
      "own_probe_cleanup_confirmed": true,
      "evidence_basis": "exact node27 owned-rank stop replies; parent additionally observed zombie state"
    },
    "node29": {
      "control_reachable": false,
      "own_probe_cleanup_confirmed": false,
      "pending_exact_targets": [
        {
          "rank": 2,
          "pid": 21883,
          "start_time": "475875814",
          "node": "node29"
        },
        {
          "rank": 3,
          "pid": 21901,
          "start_time": "475875846",
          "node": "node29"
        }
      ],
      "icmp_reachable": true,
      "http_keeper_reachable": false,
      "evidence_basis": "bounded SSH banner and HTTP timeouts; ICMP success does not prove control reachability"
    }
  },
  "evidence_ref": {
    "path": "/home/zhujianian/zhujianian/fluidckpt_zjn/codex_coding/results/v0.1.23.3/task2/resource_map/xml_recovery_r01/summary.json",
    "sha256": "3c07a3f9433d51f9739fd8c275343ac3384493bbcaad3a20f392416fb05a73d6"
  }
}
```
