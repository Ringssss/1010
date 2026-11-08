# 独立终审

已认证选定28 GPU0→30 GPU4的原串行产品执行器及两套各含2×16MiB块的同预算布局。两布局均全32MiB SHA通过，底本与缓存租约/事务保持，own allocation全部释放；两个实际worker的同communicator、IBext_v8/shca_0:1、GPU/NIC完整XML路径及精确STOP退出独立通过。

集中/目标本地分散的buffer填完为5.456453/3.956054ms，完整校验后发布为69.624693/43.226580ms，cache准备为97.579558/78.357560ms；共同源准备262.834753ms。每布局cache32MiB、底本DRAM32MiB、目标32MiB、staging128MiB，数据HBM峰值192MiB。主要发布差异发生在fill后，不归因为传输或solver收益。目标本地分散也是已有强nearest-target的同一决策。

28/30全过程0.25s观察DRAM增量为2,104,721,408/2,070,065,152B，均低于2GiB；分别超过post-warm gate投影5,816,320/344,064B。投影只覆盖当时runtime样本与应用缓冲，不覆盖全部动态开销。内核memory.peak缺失，完整物理峰值仍未证。保留64MiB remote源端超预算1,974,272B原失败、r01过期guard拒绝及较大配置prepared状态。

机器资格与逐项证据见qualification.json/evidence_refs.json；原始结果不改。formal capacity/performance readiness、大小曲线、真实模型Tready及solver决策增量均未成立。正式增量报告33.1–33.10的上述范围与证据一致。
