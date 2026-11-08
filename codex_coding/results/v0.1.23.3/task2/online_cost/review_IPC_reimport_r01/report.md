# 重复消费IPC export tuple的独立审查

开发编号v0.1.23.3/task2。code-level重复引用计费风险成立；实际部署二进制计数值由主agent限定32MiB probe验证，尚不宣称唯一runtime故障机制。reviewerGPU/SSH0。

原nativecapture已经用一个HbmTarget导入每个export handle并D2H拷贝；新增验证覆盖层再次用相同pickle构造HbmTarget，没有新的producer `_share_cuda_()`。PyTorch每次export设置一个计数，而每个新的receivedStorage释放时减一次，allocation映射缓存不会共享这两份deleter。[StorageSharing.cpp](https://raw.githubusercontent.com/pytorch/pytorch/v2.8.0/torch/csrc/StorageSharing.cpp)、[CudaIPCTypes.cpp](https://raw.githubusercontent.com/pytorch/pytorch/v2.8.0/torch/csrc/CudaIPCTypes.cpp)支持这个判断。

本机SDK2.8.0+rocm6.3的header使用uint64 counter，Python reductions的cachehit分支也释放新的producer counter。相同tuple重复消费可能使计数1→0→负值，producer unsigned视图为非零，从而无法收回allocation。这是从源码作出的推断；切换普通Torch rebuild缓存本身不能让相同counter tuple安全复用。

实际r02两rank的release_end allocated=36,496,468,992B，与release_begin只差512B；free=29,574,037,504B，对照此前actualHIPtotal可得该时刻target已用上界39,128,662,016B，仍低于48GiB。它证实初次park没有回收主要引擎allocation，与重复消费风险相符，但该较早boundary不能证明最终是哪张卡导致资源门失败。两个问题应分别保留：正确IPC消费/暂停释放，以及最终gate缺失实际card/upper/cap数值。

最小修订：nativecapture在原HbmTarget还活着时提供可选验证入口，对该同一映射gather做完整CPU源/目标SHA，之后只释放一次，最后发布captured marker。默认不开验证的旧调用保持原同步D2H契约；本任务明确启用，不把已完成内容验证移到Catalog publish之后冒称fast发布。修订需记录product capture.py来源及默认兼容性，旧129资产不覆盖。

最小probe同一export的单消费与双消费各一次，记录原计数与producer allocated/free释放前后；只由主agent在精确own容器执行，线程caps和32MiB上界保持，不作为成本校准或模型恢复样本。完整模型r02尚无C0或holdout，模型预测资格false。
