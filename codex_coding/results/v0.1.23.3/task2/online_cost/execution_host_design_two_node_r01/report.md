# 两节点真实 Qwen 执行工作流交接

当前 run 为 `online_qwen_two_node_r01`，仅 node27 GPU4/5 为 target，node30 GPU4/5 为 helper；逻辑 ranks 0/1/2/3，CPU NUMA 4/7/4/7。node28 原候选准入失败保留，本轮不创建或执行 node28 容器。

目标 DRAM 192GiB、容器硬限224GiB、初始主机 available256GiB、持续 reserve64GiB。helper 节点 DRAM96GiB、硬限128GiB、初始 available128GiB、持续 reserve32GiB；两张 helper 卡的整卡 HIP used 上界合计加256MiB余量必须不超过2GiB。目标卡初始 free 各52GiB，helper 卡各2.25GiB。所有量保留原始字节，不扣除背景 GPU baseline。

`commands.txt` 的命令依次为本地冻结、纯 CPU 新容器 setup、独立 owned HIP 物理查询 admission、主 agent 明确绑定、五进程监督与 init/bootstrap/calibrate/validate/cleanup/收集。setup 复用三节点历史容器 bootstrap 机制和厂商准确 image SHA；旧函数/临时根名字不是三节点本轮实验声明。模型只在 node27 读取配置和 readonly mount；helper 不需要模型路径。

HTTP startup 先核 session/node/gpu/BDF/boot 并接受 native rankNone，再由 controller 并发执行 mesh 赋 ranks；不会等待未初始化 rank。controller 每阶段自带 watchdog，host setup/admission/collection 有累计 deadline，每次远程命令也有 deadline。每个本任务 supervisor 继续准确 child registration、planSHA STOP 和原 process-group cleanup；停止 engine 并等待其 cleanup 完成后，再停止 worker。

每阶段创建独立 journal；out、plan、stage、bootstrap、dump、STOP、原始失败均不覆盖。setup 和 admission 对全部既有容器、原XML做前后连续性检查；run 开始和结束再次检查全部旧容器/原XML及源代码 SHA。CPU host guard 不读取 KFD/VRAM owner；真实 HIP available 与设备BDF仅在新 owned 容器短进程读取。持续 supervisor 采样共享 cgroup 和主机余量；controller 在新增分配前核主机余量、NUMA、整卡 HIP 上界。连续 HBM 峰值不由 CPU检查认证。

CPU 检查 `execution_host_cpu_r08/qualification.json` 为30项通过，包括真实 prepare 冻结当前两节点元数据至临时目录、实际 controller 五阶段 CLI dry-run、未赋rank的HTTP readiness、helper两卡总量门、冻结资源和原件保护拒绝项。临时 bundle 已回收；未执行 GPU、SSH、Docker 或远端写入。硬件实验仍须主 agent 最终审查和执行 `commands.txt`。
