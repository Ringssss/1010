# Janus 实现审计脚本

`audit_refs.py` 位于实验源码目录，默认只读核对 `implementation_r01` 的冻结报告、SHA256、行锚、baseline HEAD/clean 和锁定版本，不重写结果。

在仓库根目录运行：

```bash
python3 codex_coding/src/v0.1.23.3/task2/janus_reuse/implementation/audit_refs.py
```

原先已执行的 `results/.../implementation_r01/audit_refs.py` 保留为当次执行 snapshot，其 `qualification.json` 记录原命令，均不覆盖。后续复核使用本目录脚本，若需新增报告或证据，请新建 revision。当前只认证静态引用一致性，未认证运行能力。
