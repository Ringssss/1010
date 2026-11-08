# 节点快照准入超时的有界定位

xml_startup_owned_r01在node27 read_owned_evidence.py --node-snapshot路径外层40s超时。processes=[]，没有启动四rank、没有通信或内容传输；原始失败保留。

新增独立diagnose_inventory.py记录原node_snapshot/discover/counters/facts逐行阶段时间，15s周期faulthandler栈、80s进程alarm、容器85s timeout和外层90s上限、core=0。仅诊断进程给原discover的hy-smi子命令加60s上限，没有改原脚本、launcher、保护条件或GPU工作负载。执行相同环境setup、同session、shca0/ib0与read_owned_evidence.py入口；没有创建communicator、显式GPU buffer或执行kernel。

诊断r01因包导出discover函数与同名module的导入歧义，在读取__file__时失败，未进入discover。修为importlib.import_module后创建独立r02；r01不是GPU重试，原stdout/stderr/source/summary保留。

r02 node27返回0，外层1.573s、worker1.329s，完整snapshot JSON可解析。node_snapshot1.159s、discover0.602s；hipInit所在两行间隔约0.107s；hy-smi --showbus --json为0.0823s，--showtopo为0.1061s；counters0.00151s、facts0.121s。源码SHA和stdout/stage日志SHA在summary.json，逐项派生timing见analysis.json。运行不到15s，因此周期stack没有触发，不伪称取得慢时CPU栈。

这次未复现原40s慢，无法定位原运行究竟停在HIP、SMI、文件读取或其他步骤。单次快速重读仅支持瞬时状态的可能性，不证明具体根因。没有理由据此提高timeout或跳过保护；建议父agent按现有准入安排全新startup run/端口，若再次慢则使用这份有界诊断收集当时stack。

本次只运行node27一次成功诊断，不增加RCCL图验收范围，不修改XMLmanifest runtime_accepted=false。原startup登记的全部源码/输入SHA仍一致，见analysis.json.old_startup_frozen_inputs_unchanged。新GPU运行仍由主agent安排。
