# 修订启动合同独立审查

最终 `execution_prepared_r03`、完整八请求 CPU 回放 r02 与独立 helper CPU r02 通过 114 项、30 引用 SHA 检查。原六 worker、九进程、mode/size 序列、source、target、执行 worker 计时源码与冻结模型均保持。新增修订范围为 mode helper 与 partial-stage prepare bookkeeping；无新 GPU/SSH 调用。

真实 stage_r01 已在 node28 追加八源文件后遇到 bounded 路径 gate 失败；stage_r02 又在 node28 追加独立 mode helper、更新 prepare 后遇到主控依赖 import 失败。node30/27 两次均只有前置 readback，未追加。审查从 stage_r02 的每条 before/after SHA 递推 node28 的九个资产，与 prepared_r03 的真实 prior spec 逐项一致；其余两 node 使用真实 predecessor prior，未把已部署记录改成“全未部署”。这两次失败及旧审查 scope 缺口分别保留，旧 r02/r03 review 的 `superseded.json` 明示启动资格已失效。

本次独立检查将 **helper 原始字节完整复制为单文件**，用 `python -I` 在隔离目录执行真实 `--path` CLI，rc0 创建 cached mode 文件；重复执行返回 FileExistsError，原文件 SHA 不变；错误 prefix/parent/suffix 全拒绝。五条实际命令、stdout/stderr/rc 保存为 isolated_*.command.json。没有剥离 imports，没有依赖本仓库完整 Python 搜索路径，也没有把真实 CLI 换成 AST 函数回放。普通 CLI 只有标准库，主控 common 只在明确 self-check 分支 lazy import。

worker overlay 未引入主控 common；其原 inventory_worker、identity_diagnostics、runtime_worker 与 native worker/keeper 依赖都已在 node27 冻结 spec 中。八请求 CPU lifecycle 的证据及 GPU doubles 局限沿 r02 保留，不重跑 GPU，不将 CPU 时钟作为 GPU 性能证据。新 stage 仍须由主核实部署 readback；本审查给出 frozen package/CLI qualification，最终 GPU 数据与清理资格另行检查。
