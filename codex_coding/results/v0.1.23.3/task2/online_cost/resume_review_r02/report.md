# 实际32MiB IPC对照独立审查

v0.1.23.3 / task2。审查者仅读取已复制回本地的实测原件并查询官方源码，未执行 GPU、SSH、Docker 或新实验。24项原件一致性检查通过，44份原件及3份官方源码 SHA 见 `qualification.json`。

**一个生产者导出凭证被导入两次，确实使生产者在消费者退出和 del/GC/empty_cache/ipc_collect/sync 后仍保留全部32MiB；单次导入释放成功。** 这是当前实际部署下的小块受控复现，支持保留单映射修复并进入新完整模型的物理暂停验证。

| 实际指标 | once_r02 | twice_r02 |
| --- | ---: | ---: |
| producer share调用 | 1 | 1 |
| raw import次数 | 1 | 2 |
| 全32MiB字节比较 | 通过 | 两次均通过 |
| consumer退出/清理 | rc0/通过 | rc0/通过 |
| drop后allocated/reserved | 0/0B | 33,554,432/33,554,432B |
| drop阶段整卡free增长 | 33,554,432B | 0B |
| 所有采样最大整卡upper | 725,614,592B | 725,614,592B |

两配置独立生产者；初始、share后和consumer退出后的GPU样本逐项相同，均在原2GiB诊断门内。实际 Torch 版本均为 **2.10.0**；旧审查引用的本地 SDK `2.8.0+rocm6.3`不代表该容器部署版本，后续文档应明确更正而保留旧原件。

**现有数值计数读法无效，不能声称实测看到1→0→−1。** 探针当前以 `offset*8`作文件偏移；官方v2.10.0的 `RefcountedMapAllocator`把数据指针放在64字节头之后，文件头为分配器自身映射引用计数。结合实际handle offset=0，恒定读到1与读了该头部一致；正确上游IPC槽位置为 `64+offset*8`。该发现是源码与探针布局的推断，未确认海光部署二进制的全部vendor改动。原计数行保留，解释标为无效而不回填数字。[官方 MapAllocator.cpp](https://raw.githubusercontent.com/pytorch/pytorch/v2.10.0/aten/src/ATen/MapAllocator.cpp)

官方v2.10.0仍为每次导出设置计数1、每个received storage的deleter减一次，对非零unsigned值保留allocation；源码支持重复消费风险。但本轮直接确认的是分配保留差异，实际计数下溢和旧完整r02故障的唯一归因尚未证明。[官方 StorageSharing.cpp](https://raw.githubusercontent.com/pytorch/pytorch/v2.10.0/torch/csrc/StorageSharing.cpp)、[官方 CudaIPCTypes.cpp](https://raw.githubusercontent.com/pytorch/pytorch/v2.10.0/torch/csrc/CudaIPCTypes.cpp)

fresh inspect确认旧准确CID的 `IpcMode=private`、1GiB SHM且无 `/dev/shm` bind。结束后 `post_probe_processes.txt`仅sleep PID1和采集ps；`stopped.json`显示Running=false、PID=0、OOMKilled=false，私有namespace最终停止。退出137为sleep PID1的stop处理，不能称OOM。完整模型不得复用该探针容器、旧运行目录或旧冻结资产。

下一步只需新源码/新容器的完整Qwen capture→真实park两维90%门，再依原协议helper/pathcal/C0/事前冻结Hnear/Hremote。无需为数字计数另开硬件才准入。小探针不赋予完整模型释放资格或整体10%预测资格，正式成本样本仍0。
