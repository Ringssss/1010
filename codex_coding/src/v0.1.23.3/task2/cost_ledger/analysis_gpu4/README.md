node27 GPU4/BDF0000:85:00.0/NUMA4 的独立成本分析域。analyze.py 从原冻结 analysis/analyze.py 逐字复制，只更改 EXPECTED_STATE 中 configuration_id 与 topology 两个字符串；test_analysis.py 逐字节复制，随本目录 import 新域。原目录及其冻结不修改。

96MiB source、logical worker0、native模式、校准/留出顺序、OLS、全部数值门和停止条件保持。原GPU0失败无校准数据，不能迁入任何系数。最终源码/仪表/协议冻结由主 agent 在独审 C0 通过后建立。
