# ServerlessLLM Store 海光 HIP 数据面适配

- 官方固定 commit：`9f50241baa5386e06a9321c51f19a9ef5f964c2b`。
- 原 checkout `lib_cite/ServerlessLLM/` 不变；独立 detached worktree 为 `lib_cite/ServerlessLLM-hip/`。
- `dtk_gfx936.patch` 只增加 gfx936、兼容 DTK 的小写 hip CMake 配置发现、允许使用已有 glog、仅在测试启用时获取 googletest，并移除未使用的 BLAS 头。未改 Model 数据读取、chunk dispatcher 或 H2D 实现。
- CPU 隔离构建容器：`zjn-serverless-build-r01`，4 CPU / 16 GiB，无 `/dev/kfd`、`/dev/dri` 映射，不改共享 Python 包。
- DTK 的 HSA 头与 runtime 位于 `.hyhal`；构建脚本显式指定路径。隔离容器补充 `librt.so -> librt.so.1` 是兼容 MIOpen 已有 CMake target 的缺失路径。
- 构建命令：`python3 codex_coding/src/v0.1.23.3/task2/baseline_loading/serverless/build_store.py --out <唯一构建结果目录>`；GPU gate 与完整恢复由主 agent 单独排程。

本对照为官方 Store 数据面 HIP 适配，不包括 ServerlessLLM 在线请求调度、启动选址或迁移服务。输入为已处理 TP 权重，格式建立与保留完整 host cache 的空间独立计账。

## 最终实际验收（2026-10-07）

`codex_coding/results/v0.1.23.3/task2/baseline_loading/serverless_qwen_full_r03/summary.json` 已通过完整 Qwen TP2：主指标 `max(weights_ready)-min(load_begin)=131.121134861s`，完整 70,329,718,112 字节的两 rank SHA 匹配冻结 S2 源、生成相同、worker正常退出、代码无漂移、源元数据保护和物理 HBM 回收均通过。不是仅源码获取或 CPU 估价。

Store 额外完整 pinned pool 为 66 GiB，setup 40.270199s 单列，目标 storage 由既有引擎分配，无额外整模型 HBM staging。输入与原生模型使用同一 NFS 介质；运行时 OS/NFS cache 未控制、未测量。上游尝试 O_DIRECT，最终日志未出现 fallback，不将此解释为物理冷/热缓存判据。

Python adapter 的兼容修正为严格解析 PyTorch version2 malloc 的66B frame为原生64B IPC handle，以及在后验 hash 每 rank 导入前初始化/选择 Torch device；上游批量加载 C++ 路径没有因这些修正重写。后者以独立24KiB current/set_device单变量 probe确认139→0、全字节/SHA/guard通过。失败 run 与源修复前 SHA 快照均保留。

补丁 SHA256：`0cf91f91e9babee3931acd7083bc7fe4d60528311974f451b2e411446c2eb9d0`。

- `_C` 二进制 SHA256：`10314c4467f571e9bbc557685c513a62a3622ddd6c2b3aae35f9d466d6dfcefd`。
- `_checkpoint_store` 二进制 SHA256：`c58c630a8c747b066ea12cb1816286ae94df6e6ea158b1f0f0f6fdd1160d460e`。
- Torch：`2.10.0+das.opt1.dtk2604.2608131652.g28498c`；HIP `6.3.26113`；Python `3.10.12`。
- 构建与实际容器 Image ID：`sha256:ee3b9807d9e90cdc3e6ab9c1058f0b78dabcafa6a0fc7661d9c5af69ce1f9588`。

独立 CPU build 容器已在身份核对后清理，记录位于 `serverless_build_r01/build_cleanup_{identity_before,remove,after}.txt`；原 frozen checkout、有效 NFS 数据、fixture keeper与已恢复的 parked 引擎保留。
