# Admission argv 局部修复

原 admission r01 在创建新 GPU 查询 CLI 之前收到 OS 的 Argument list too long；包含源码 base64 的完整 setup spec 不应作为查询 argv。失败原件保留，未将其计为 GPU 初始化。

主控现在仅传 node、roles、minimum_initial_gpu_free_bytes、per_selected_gpu_hbm_cap_bytes、task_hbm_cap_bytes；roles 只保留 gpu/BDF/PCI NUMA/host NUMA。白名单 JSON 和完整查询 argv 均严格小于16KiB。新主控 SHA 与原冻结 setup SHA 另存 sources.json。原129项远端冻结资产无需部署或变更。

针对性10项 CPU 检查通过，包括真实冻结 setup 输入缩减、argv大小、物理角色和预算保持、target52GiB free、helper两卡全球 used 合计及256MiB余量门。所有物理调用均为 mock，没有 GPU、SSH、Docker 或远端写入。commands.txt 提供 r02 admission 命令，绑定成功的 setup r02；后续 bind 应引用 admission r02/admission.json。
