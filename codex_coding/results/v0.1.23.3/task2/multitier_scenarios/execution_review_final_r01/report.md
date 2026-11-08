# 三节点绑定与接口独立验收

119项独立条件及60条SHA/scope核验全部通过，r01合列179条。当前资格覆盖最终`mapping_binding_r03`物理候选、原产品串行CPU API与失败精确清理、node27新GPU0/BDF09单次1MiB实机资格。[分列计数和详细报告](../execution_review_final_r02/report.md)只调整呈现，证据与结论一致。

node27的新boot与旧PID缺席已核，实际1MiB全SHA正确、自然rc0、无signal/timeout、进程组空；容器恢复pids1且无新增OOM，三个旧停止容器身份与状态保持。19.990555s包括初始化。selected GPU时点total-used+1MiB=338,690,048B小于2GiB，完整峰值仍待测量。

最终六角色为28GPU0/1、30GPU4/5、27GPU0/4，NUMA分别0/3、4/7、0/4。本地NVMe父mount与容量已绑定。原2GiB为旧小实验暂定gate；新场景DRAM6GiB/node、HBM2GiB/node按runtime及应用聚合计账，旧超预算失败保持。

native CPU79项与journal7项证据通过；独立API41项拒绝fixture改physical、过期/错SHA/错epoch-generation/预算漂移及底本混入清理。部分恢复失败时12拟建ID提前记账，9个已建ID精确回收、3缺项跳过，八SSD底本VALID/lease0。SHA只验证输入完整性，物理启动仍需独立可信协调gate。

下一具体输入为第二卡/六worker会话、NUMA权限及触页注册、三本地SSD服务和16/8/8MiB底本manifest、postwarm聚合预算与可信gate；之后SSD_only/C0/C1/C3各恢复一次。当前single-copy容器只有IPC_LOCK，其NUMA资格待补。六worker/三SSD恢复/性能/模型T_ready均保持false。

验证入口在`src/v0.1.23.3/task2/multitier_scenarios/execution_review/`，详细命令和各结果目录见r02报告。独立审查GPU/SSH/远端写入均0；全部引用已复制为不可变快照，正常文档更新不影响验收。
