# 有界成本模型：独立启动审查

编号v0.1.23.3/task2。结论为`launch_ready`，当前无静态启动阻塞项；仅允许主agent执行既定新session的两个请求，各一次。审查未执行GPU、SSH或远端写入。

正式模型`model_frozen_r01/frozen_model.json`的SHA为`e98bbed04d5ce502439acea4fd8fabd554dd67e69ca2b94ebba784ab8535734f`。独立审查从原训练/规定重复重算本地常数、两条正斜率系数、worker共有固定项及controller残差，22项算术/有效域拒绝检查通过，结果在`review_model_r01/qualification.json`。新launch的62项来源、环境、配额、身份和append-only资产检查通过，结果在同目录`qualification.json`。

本地仅单op4–16MiB、每path至多一op；1MiB×4锚点未泛化。request属于worker外层，dispatch属于controller减最大worker的残差，两者各加一次。旧36MiB组合和新holdout未入fit。本地原负斜率、bandwidth未知状态保持；remoteDRAM/SSD复用原service_s拟合。正式published预测为null，实际校验/发布及完整请求耗时保留；full-filled只作训练控制中位数粗迁移假说，独立服务和execute误差另报。

源码关键行为审查已核：六worker物理BDF/PCI/NUMA/HCA/通信配置与旧postwarm逐项对比，当前boot/PID/start/epoch独立检查，实际warm24/45重新建立。源创建后才bind；HBM/SSD新bootstrap完整16MiB SHA与旧训练底本相同，DRAM仅确认冻结generator/seed/schema等价，明确没有跨session完整旧SHA对比，每新请求独立全字节校验。两个8MiB预测在首请求前共同冻结，前后核模型/预测SHA；请求起点早于fresh inventory/gate/plan，同controller clock_domain显式核对。

新包七个实验py严格追加，旧资产SHA、容器/boot与输入XML保持。active monitor只解除idle谓词、仍核身份/预算，确定失败统一STOP，最后核own Catalog/used/事务/SSD清零和九owned进程退役、旧owner/container及全部资产SHA。DRAM6GiB/HBM2GiB聚合配额与deadline保持，node29不访问。

`launch_ready`仅指代码/CPU/冻结包符合本轮范围，不认证当前实机warm、预算峰值、holdout准确度、共享流容量、live模型T_ready或placement决策排序。两请求不是同预算候选对照，`decision_ready=false`保持。

中间`review_model_draft_r01`因predictor在draft之后修订，原SHA校验正确拒绝过期draft；失败记录保持。最新正式模型及review均通过，没有通过覆盖失败或选择训练点实现验收。

独立复核命令：

```bash
python3 codex_coding/src/v0.1.23.3/task2/bounded_cost/review/audit_model.py --model codex_coding/results/v0.1.23.3/task2/bounded_cost/model_frozen_r01/frozen_model.json --out codex_coding/results/v0.1.23.3/task2/bounded_cost/review_model_r01
```

该输出目录已使用，不可原地重跑；未来复核应使用新目录。
