# 完整r03冻结资产快速审查

本地只读审查；GPU/SSH/Docker调用0，未重跑35项CPU检查。检查记录见 `qualification.json`。

两个138资产bundle内部payload与声明SHA一致；节点间资产SHA一致；旧独审的21个execution文件在该冻结快照中全部保持原SHA。单映射capture为 `2e39205d3fea6bd2ebe38e29a4442a8b8d4e13ae24cfdf1aa239feb406a97c93`，subreaper与controller/worker修复均纳入。

四worker仍node27/30的GPU4/5、BDF85/b5、NUMA4/7，engine TP2/basegpu4，全角色八卡visibility及导入前线程限制为1。target任务DRAM192GiB/hard224GiB、HBM48GiB/card；helper任务DRAM96GiB/hard128GiB、HBM聚合2GiB不变。新容器身份待绑定，输入与dump XML分开、模型只读挂载、IPC private，setup wrapper来源及SHA可追溯。

严格“全部资产等于当前工作树”两项未通过：审查时并行修正了诊断用 `probe_ipc_counter.py`与`check_ipc_probe_cpu.py`，快照仍保存旧SHA `b2b7429e...`/`59a389a6...`，当时工作树为`f9b33b25...`/`feb44c4d...`。这不改变完整模型核心冻结资格，但不能将138资产全部称为当前最新源码。该快照/审查原件保持；下一新局部运行应新冻结包含最新探针修正的源码。

随后主agent实际准入发现node30两helper卡高占用，聚合2GiB门失败，因此完整两节点在线成本运行不可启动，不改预算。

可以在新的明确局部验证run中，仅使用node27两keeper和TP2 engine完成真实完整capture→reference→release_live→真实park两维90%门，在checkpoint_parked立即结束。需要新局部mesh/绑定/namespace/冻结源码，跳过远端warm、helper/cache、pathcal/C0和留出；复用原target预算，不产生在线成本资格。不能直接调用原setup继续其硬编码四role/helper路径。局部释放通过仍不认证两节点成本模型或完整10%误差。
