# 独立离线审查入口

只读取本地文件，不import待审产品验证器，不发送SSH/HTTP、不启动GPU。开发编号固定`v0.1.23.3/task2`。

`inspect_existing.py`保存历史profile与当前overlay的来源/指纹摘要；`report.py`重算实测流速率、同主机并发窗口、IB端口计数字节、原始pre/post保护、退出PID与物理回收，并核对场景冻结的输入/代码/case/manifest指纹、预算及每rank完整覆盖。

```bash
python3 codex_coding/src/v0.1.23.3/task2/topology_scenarios/audit/test_report.py
python3 codex_coding/src/v0.1.23.3/task2/topology_scenarios/audit/report.py --calibration <calibration_run/summary.json> --freeze <freeze_run/manifest.json> --out codex_coding/results/v0.1.23.3/task2/topology_scenarios/audit_final_NEW
```

输出目录必须不存在，原始run不可覆盖。校准未完成时可以只给`--freeze`，其通过意味着冻结契约可核验，不能宣称应用执行器、预测校准或物理S5已通过。新GPU样本固定为0；引用校准观察独立列出，每case不能重复记样本。

配对聚合吞吐用总bytes/共同makespan；每流速率之和只作单流参照。退化判据是`max(匹配单流耗时)/并发共同makespan<=0.85`，避免异速独立流被速率和误判共享。起始skew超过最短单流耗时5%时保持conditional；每流slowdown和起始skew同时列出。80%overlap是已同步调用区间门槛，含等待，设备kernel活动重叠需额外trace。实测effective GB/s不是真实capacity upper bound。原始端口计数含其他用户流量，方向/单位/载荷核验不构成独占流量归因。

独立验收不会把`functional_provisional_assumption`预览升级为性能证据。微probe执行器与keeper_serial不同，应用物理共享门槛继续保留。候选同操作签名时不新增完整模型恢复观察；下一步e4/e5需同预算强简单placement对照和新完整模型正确性/输出/清理验收。
