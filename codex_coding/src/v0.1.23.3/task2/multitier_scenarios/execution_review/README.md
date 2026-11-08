# 执行审查

`build_minimum_plan.py`冻结三节点六GPU与三本地SSD的最小功能序列，复用已有32MiB合同见证。输出目录必须全新；不远端执行、不启动GPU。

预算出处、四次最小恢复和逐node ledger见`results/v0.1.23.3/task2/multitier_scenarios/execution_review_minimum_r02/`，引用按当时内容保存快照。

`audit_preparation.py`审静态物理角色与新空容器setup；`audit_adapter.py`核原生CPU evidence、源码delta及独立负路径/精确失败清理；`audit_runtime.py`只读主agent已执行的node27 1MiB原件。`finalize_review.py`将119个独立条件和取证/scope项分列并复制不可变快照，最终产物在`execution_review_final_r02/`。子agent入口均不启动GPU或SSH。
