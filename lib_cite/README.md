# Baseline 源码材料

本目录为 `v0.1.23.3/task2` 保存四个独立 Git 仓库，用于基础场景建设、源码核查和后续适配时的上游参照。当前状态均为 `checked_out_only`：已取得源码并冻结版本，尚未安装依赖、编译、加载模型、运行 GPU 实验或完成系统复现。

版本和获取证据见 [baselines.lock.json](baselines.lock.json)。父仓库跟踪说明、锁定记录及将来的 `patches/`，四个嵌套源码仓库由各自 Git 管理，不能依靠克隆父仓库恢复它们。

## 来源、版本与用途

| 路径 | 官方来源 | 冻结 commit | 本项目用途 | 许可文件 |
| --- | --- | --- | --- | --- |
| `sglang/` | [sgl-project/sglang](https://github.com/sgl-project/sglang) | `70f0b7351e74b372cc928db533824d3993d65922` | 原生加载与 Weight Cache / IPC 源码参照 | `sglang/LICENSE` |
| `Janus/` | [Janus2026/Janus](https://github.com/Janus2026/Janus) | `ec1beca2dbef5628b38e266fdc9afa0dba451678` | warm / D2D fork、生命周期与 xTensor 源码参照 | `Janus/LICENSE` |
| `blitz-scale/` | [blitz-serving/blitz-scale](https://github.com/blitz-serving/blitz-scale) | `594e91127714349c183d935ac68d7b0eb63fc1ed` | 参数池、来源组织与拓扑分发策略核查 | `blitz-scale/LICENSES/Apache-2.0.txt`、`blitz-scale/NOTICE` |
| `ServerlessLLM/` | [ServerlessLLM/ServerlessLLM](https://github.com/ServerlessLLM/ServerlessLLM) | `9f50241baa5386e06a9321c51f19a9ef5f964c2b` | 多层加载、缓存局部性与选址机制核查 | `ServerlessLLM/LICENSE` |

SGLang 与 Janus 使用此前已审查的指定版本；BlitzScale 与 ServerlessLLM 使用 2026-10-06 获取时官方 `main` 的 HEAD，尚未核实为对应论文的 artifact 版本。这些冻结版本是研究起点。正式论文 baseline 准入时应核对论文 artifact 身份、适配期间的上游变化和可比协议；更新版本须显式记录，不能静默替换。

关键入口：SGLang 的 `python/sglang/srt/weight_cache/`；Janus 的 `janus_engine/`、`janus_service/`；BlitzScale 的 `src/`、`router_v2/`、`config/`；ServerlessLLM 的 `sllm/`、`sllm_store/`、`benchmarks/`。完整研究边界见 [baseline 研究路线](../docx/articles/2026-10-06_baselines_delta_and_research_roadmap.md)。

## 获取与依赖状态

四仓库均以 depth=1 的浅历史、detached HEAD 检出，`origin` 指向官方 URL，工作区干净。未递归获取 submodule，使用 `GIT_LFS_SKIP_SMUDGE=1` 避免 LFS 大文件下载，也未下载模型权重。

Janus 初次获取复用了本机此前源码审查的对象，通过 Git `file://` transport 配合 `--no-local --depth=1` 克隆，随后将 `origin` 改为官方 URL。其对象已完整复制到本目录自身的 `.git/objects/`，没有 alternates、shared object store 或临时目录运行依赖。其余三仓库从官方 URL 直接浅 fetch 精确 SHA。

BlitzScale 的七个 submodule 均未初始化：`request-sim`、`src/kernel/flashinfer`、`third_party/{argparse,fmt,googletest,json,pickle}`。这些目录只是 Git 占位，不能据目录存在判断依赖已就绪。其他三仓库没有 Git submodule 条目；它们随源码携带的第三方文件也不代表依赖已安装。

本次没有执行上游构建或运行指南。冻结版本的 BlitzScale README 明确列有 WIP 构建/运行说明以及 token 正确性的已知问题；这里记录上游状态，尚未进行本机验证。其他仓库的运行能力同样待后续准入检查。

## 原版与适配边界

当前四仓库保存未修改的官方源码。后续先映射已有 FluidCKPT 组件，再决定必要的适配范围：

- 完成固定版本官方实现的功能与配置验收后，才将结果命名为该系统原版对照。
- 修改官方实现以支持 HIP/海光时，命名为对应系统的适配版，并保存基准 commit、补丁、配置及行为变化。
- 在共同执行器中复建机制时，命名为“Janus 式 warm/D2D 机制对照”或“依据 BlitzScale 的策略适配”等，并按实际能力报告结论。

已有 HIP IPC 适配不代表官方 SGLang daemon 已复现；本项目 greedy 策略不代表 BlitzScale 系统。适配源码应建立独立分支或 worktree，补丁可归档至 `lib_cite/patches/`，同时保留本锁定版本的未修改参照。依赖安装、模型加载和实验安排按原 e1–e8 协议另行执行。

## 恢复和核查

在项目根目录执行以下 Bash 命令，可仅从官方来源恢复缺失的四仓库。函数遇到已有路径即返回失败；已有仓库先查看 `git status` 和 HEAD，保留本地改动后再安排恢复。

```bash
mkdir -p lib_cite
restore_baseline() {
    local baseline_path="$1" baseline_url="$2" baseline_commit="$3"
    if test -e "$baseline_path"; then
        printf '已有路径，请先检查：%s\n' "$baseline_path" >&2
        return 1
    fi
    git init "$baseline_path" &&
    git -C "$baseline_path" remote add origin "$baseline_url" &&
    git -C "$baseline_path" -c submodule.recurse=false fetch --depth=1 origin "$baseline_commit" &&
    GIT_LFS_SKIP_SMUDGE=1 git -C "$baseline_path" -c submodule.recurse=false checkout --detach FETCH_HEAD
}
restore_baseline lib_cite/sglang https://github.com/sgl-project/sglang.git 70f0b7351e74b372cc928db533824d3993d65922
restore_baseline lib_cite/Janus https://github.com/Janus2026/Janus.git ec1beca2dbef5628b38e266fdc9afa0dba451678
restore_baseline lib_cite/blitz-scale https://github.com/blitz-serving/blitz-scale.git 594e91127714349c183d935ac68d7b0eb63fc1ed
restore_baseline lib_cite/ServerlessLLM https://github.com/ServerlessLLM/ServerlessLLM.git 9f50241baa5386e06a9321c51f19a9ef5f964c2b
```

若指定 SHA 不再能从上游取得，报告失败并保留锁定记录，不改抓最新 HEAD。浅历史足以保存当前源码；后续若需历史分析，可另行加深历史，保持冻结 HEAD。

```bash
for baseline_path in lib_cite/sglang lib_cite/Janus lib_cite/blitz-scale lib_cite/ServerlessLLM; do
    git -C "$baseline_path" rev-parse HEAD
    git -C "$baseline_path" remote get-url origin
    git -C "$baseline_path" status --short
    git -C "$baseline_path" rev-parse --is-shallow-repository
    test ! -e "$baseline_path/.git/objects/info/alternates"
done
git -C lib_cite/blitz-scale submodule status
```

首次核查：四个 HEAD 均与锁定值一致，官方 origin 正确，工作区干净，关键入口及许可文件存在，无 alternates；源码共约 625 MiB。该核查只确认材料完整性。

## 2026-10-07 运行适配进展

首次获取记录及四个原始冻结commit保持不变。后续运行身份另列，不把原checkout的`checked_out_only`改称整系统复现。

| 路径 | 实际身份 | 验收证据 |
| --- | --- | --- |
| 海光SGLang native | 安装vendor包的真实DefaultModelLoader，非本目录冻结上游原版 | `baseline_loading/native_observed_r01`：Qwen TP2全SHA/输出/清理通过，T_ready122.266963s，文件cache为observed/mixed |
| BlitzScale | 冻结源选择/单目标TP分发策略接HIP/RCCL共同执行器 | `baseline_loading/blitz_s3_r02`：T_ready2.804379s，全SHA/源前后输出/回收通过；不是完整CUDA/RDMA/多目标pipeline |
| ServerlessLLM Store | 独立HIP worktree编译真实上游C++数据面，外部目标IPC适配 | `baseline_loading/serverless_qwen_full_r03`：T_ready131.121135s，全SHA/输出/物理回收通过；两次完整失败及小数据定位见正式报告第21节 |
| Janus | 现有host/D2D机制对照 | 原版Janus/xTensor整体未完成海光复现，原始源码未修改 |

各结果目录均位于`codex_coding/results/v0.1.23.3/task2/`。Blitz策略、Serverless构建补丁分别见`patches/blitz-scale/README.md`和`patches/serverlessllm/README.md`；原版源码不覆盖，工作副本分别为`blitz-scale-hip`和`ServerlessLLM-hip`。格式、初始驻留层级、缓存与准备成本不同，不直接以这些时延比宣称算法优势。
