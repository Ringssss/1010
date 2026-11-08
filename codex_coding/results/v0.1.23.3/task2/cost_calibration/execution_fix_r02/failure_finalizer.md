# r01 失败终结说明

原件 `execution_actual_r01/measure/journal.json` 保持原 `running` 状态，不改写。原父监督器 `measure_controller/command.json` 明确记录 controller PID 757211、`active transaction at gate`、`returncode=-15`，说明父进程的资源 monitor 在正常事务进行时错误复用了只允许空闲的准入谓词，并以 SIGTERM 停止自己创建的本地 controller。controller 未完成 Python finally，因此原 journal 未终结，不能把它当成功或无清理遗留的证明。

`execution_actual_r01/summary.json` 已由原父进程正常终结：failed，56.192751秒；九 STOP 标记 rc0，九 supervisor rc0，三节点 owned collection passed。它不是完成校准或验证的运行；原 partial 训练和 pending 记录保留，r02 不使用这些点拟合。

原 r01 对库解析假说的无 payload 实机定位证据仍有效：legacy 两次完整六 worker 库存中位数79.063262ms，cached 12.129493ms，库解析/加载最大worker分段下降61.084511ms，精确静态身份保持，预登记归因门通过。这个 A/B 不等于完成恢复事务速度验证。

r01唯一自有SSD源为 `costcal_runtime_r01/source2/ssd`，对应 `e4cb7634432fe4405018f096a672e7185a6f7431044e41172c2f95789718c6e3.bin`，16MiB。只读核查 `execution_ssd_readonly_r01/summary.json` 已匹配 bootstrap2 的每字节 SHA、quiet 容器 CID/StartedAt、boot 和宿主挂载；不能通过重启/新session掩盖这个残留。主 agent 执行独立 exact unlink，结果另存，不清目录和其他文件。

r02仅修复这个实际触发错误：请求 gate 默认 require_idle=true，live monitor require_idle=false，其身份校验视图跳过空闲谓词，原 transactions 完整保留进证据。所有原精确身份与 DRAM/HBM 检查继续执行。新增 CPU 检查确认：准入仍拒绝 active transaction，monitor 接受并记录 active transaction，monitor 仍拒绝错误 BDF。

旧11源码从 r01 冻结追加包完整保存于 `source_previous/`。r02新 session、输出和 source snapshot 独立；prior setup 使用实际已部署 r01 源 SHA。原产品、实验原件、输入 XML 不改。
