# node27 准入失败的有界 metadata 诊断

本轮只执行获授权的 metadata-only 一次对照；没有 Torch、GPU payload、NCCL communicator、模型权重读取或 node29 操作。原 local_xml_node27_r01 保持 failed/0rank，旧显式源码与 raw summary SHA 前后全部相同。

| case | 可见范围 | 实际事件边界 | 退出 | 时间 |
| --- | --- | --- | --- | --- |
| all_visible | HIP/CUDA/ROCR visible 三项均 unset | libgalaxyhip.so.5 加载返回，hipInit_enter 后触发10s alarm，未hipInit_exit | rc1，TimeoutError | 10.1315s |
| gpu01_visible | HIP/CUDA=0,1，ROCR unset | library_load_enter 后未library_load_exit，未hipInit_enter | SIGSEGV(-11) | 0.3642s |

每组只一次，early alarm=10s、child hard=12s、exact cleanup最多2s。子 PID13691/start476868455、13698/start476869468 均已退出，core limit0；container supervisor和host sysfs子进程也已退出。全部cleanup通过。第二组尚未进入hipInit，不能解释成两组都hipInit卡住，也不能把先后差异直接归因为visibility；本轮不扩矩阵或重新启动GPU。

sysfs/KFD约7.6ms完整读取GPU0=0000:09:00.0/NUMA0/renderD128、GPU1=0000:36:00.0/NUMA3/renderD129；KFD node8/9及GPU ID22078/34430匹配。无需SMI或HIP新初始化即可取到物理身份和total/used。keeper0/1 compact RPC分别6.04/4.67ms返回新fresh sample、原epoch和空transactions。

| GPU | sysfs total-used B | keeper HIP physical free B | 前者减后者 B |
| --- | ---: | ---: | ---: |
| 0 | 5,009,002,496 | 4,974,968,832 | 34,033,664 |
| 1 | 5,555,335,168 | 4,767,350,784 | 787,984,384 |

两个采样相隔约0.1s且存在共享负载，指标语义也不同。新鲜keeper余量查询可靠返回，但sysfs的total-used不能静默替换HIP Available/free，不由这个观察绕过新的HIP初始化失败。host /proc FD owner扫描完成但2566权限错误、0候选，owner归属仍缺证；0候选不代表GPU空闲。完整原始sysfs/KFD、owner errors、keeper inventory、事件及PID退出证据均单列。

关键命令：

~~~bash
python3 codex_coding/src/v0.1.23.3/task2/resource_map/local_xml/diagnose_admission.py --out codex_coding/results/v0.1.23.3/task2/resource_map/local_xml_diagnosis_r01
~~~

未改变准入、未增加原run timeout、未重跑原probe。后续仅按父授权保存旧源码并修正已独立确认的catalog tombstone误拒；GPU启动仍停止。diag的SIGSEGV仅定位到库加载阶段，不认证XML实际接受/拒绝，也不认证NIC/GDR或性能。
