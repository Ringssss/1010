# Store worker139：成熟IPC路径的只读差异

`serverless_qwen_full_r02`已经产生同host主窗口132.324249761s和正确参考文本，但worker_exit_code=139，未完成目标全SHA，物理回收false。因此只保留observed/unaccepted，不认定Store新性能验收。本笔记仅给24KiB微probe的控制变量，不宣称根因。

| 变量 | 当前hash_target | 成熟keeper/HbmTarget | 可区分的probe |
| --- | --- | --- | --- |
| current device | serverless_adapter.py:141在第一次_new_shared_cuda前没有set_device | keeper.py:108先set_device(gpu)，verify_run也先set_device | Store后打印current device，首次rank0导入前仅增加同一set_device |
| allocation lifetime | 每storage导入、hash、del tensor/storage | 先导入全部entries，并self.segs保活整个tx；随后hash，最后finish detach | 保持逐项释放与全映射保活两组，保持相同producer数据与Store |
| raw importer与Torch importer共进程 | upstream先raw Open/Close，随后该process做Torch _new_shared_cuda | keeper只经Torch导入，映射不与raw importer交替 | 相同Store写入后，当前process hash与独立fresh process hash |
| producer refcounter tuple | 一个export字段代表一次PyTorch发送/消费者生命周期 | 一次phase export、一次保留mapping，清理有序 | 每probe/消费者生成新的_share_cuda_ tuple，不复用前case已被Torch消费并释放的同一Refcounter |

具体源码：CheckpointStore构造在`checkpoint_store.cpp:50`附近主线程遍历GPU0/1调用cudaSetDevice，结束可留下GPU1；`hash_target`从rank0开始却没有显式set_device。Torch新共享storage和tensor.cpu可能内部有device guard，因此这只是明确差异，需要probe判定。

已安装vendor Torch `torch/multiprocessing/reductions.py:256–268`说明同进程多次open/close一个allocation的IPC handle可能得到新地址，关闭后旧storage地址不可继续使用，因而实现需要缓存allocation指针并保持既有storage存活。这里提供全映射保活probe的代码依据，不能由CUDA文档注释直接证明本HIP139根因。

真实full r02每rank1009storages、209allocation、801非零offset；比小gate的3storage/1allocation复杂。成熟HbmTarget也按storage导入，差别是把所有tensor强引用保存在self.segs。`Cluster.target_digest`只读取这些既有tensor，不在hash循环重新开关IPC mapping。

Store在同步H2D future全部结束后置LOADED，随后rawClose；目标engine仍应持续持有exporter storages。C++直接raw importer不会走Torch Refcounter tuple的消费者协议；full目标SHA使用Torch的新importer。若139发生在import、D2H还是del/destructor，必须由faulthandler/flush阶段日志定位，不凭退出码猜测。

已经将这些差异交SLLM负责agent用于same-process/fresh-process小probe；没有改其实现、启动GPU或重读70GBStore数据。
