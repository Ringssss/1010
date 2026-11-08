# localHBM 负斜率的原件与路径审计

旧实验的反常差量确实发生在 worker 的单操作 record 中：4MiB 两次为 329.504/333.784µs，16MiB 为 240.403/219.493µs。它不是 controller 请求固定项造成的假象。当前代码及原计划支持两种尺寸都经过一次连续 HBM→HBM `copy_` 和一次设备同步；没有因 synthetic 的 t0/t1 分段而增加小尺寸拷贝次数。

## 原始记录

|配置|record(µs)|worker execute(µs)|execute减record之和(µs)|
|---|---:|---:|---:|
|4MiB原测|329.504|378.205|48.701|
|16MiB原测|240.403|280.804|40.401|
|4MiB反序补测|333.784|384.496|50.712|
|16MiB反序补测|219.493|273.913|54.420|
|4×1MiB原测|338.995 / 83.381 / 77.341 / 72.791|712.510|140.002|
|4×1MiB补测|258.584 / 89.302 / 91.441 / 73.051|638.549|126.171|

两个 batch 的首个 1MiB record 分别是随后三个平均值的 4.355 倍和 3.057 倍。这是当前最直接的首操作效应线索。batch 同一 execute、同一 source、同一大小，随后三个有不同 source offset 与独立 target；因此它指出需要定位的边界，但尚未单独控制地址和目标首触达。

## 实际路径与计时边界

- 原计划为 `mode=replica`、`tier=hbm`、w4→w4、serial、32MiB chunk。six case 的 source ID、generation、identity、epoch 相同。源为完整16MiB flat byte buffer。
- `prepare` 第173–183行将 replica 交给 Catalog/allocate；`allocate` 第109行返回 `torch.empty(desc['n'], uint8, self.dev)`。目标各自拥有 flat tensor。Catalog 第26行4096B取整仅用于容量账目，不插入 tensor storage padding。
- synthetic index 为 t0=[0,1MiB]、t1=[1MiB,15MiB]；`_execute` 和 `land` 均不读取 index。`HbmTarget.scatter` 只由 live target 触发；旧实验没有 live target，也没有 scatter。
- `_execute` 第238行 stamp 后执行第239行 begin log，再构造 source slice，调用 `land` 的目标 slice+`copy_(non_blocking=True)`，然后第260行 `torch.cuda.synchronize(self.dev)`。4/16MiB都小于chunk，因此只有一次循环、一次copy调用和一次同步。此处是源码推导；旧原件未记录实际HIP底层API/拷贝引擎。
- record 包含 begin log 的 JSON序列化、event append 与 `print(...,flush=True)`；末尾 end log 在该 record 时间戳采样之后，进入 worker execute 剩余部分。live metrics emit 分支对这些 replica 不写文件，原 native execute profile 条件也不触发。
- Handler 第408行和 `_execute` 第218行的 `set_device` 都在 started/stamp 之前。不能把二者直接算进 old record。随后第一次 source slice/copy 所触发的每线程运行库操作仍可能在stamp内；其存在与耗时须实测。
- 服务使用 ThreadingHTTPServer。当前本地Python3.10源码的 ThreadingMixIn 对每个连接创建新的 `threading.Thread`；远端实际TID没有旧记录，诊断应实际输出。单个batch四op共享handler，跨请求prepare/execute不会复用同一个handler调用链。
- 第238行stamp前没有GPU同步。每chunk末尾是device-wide synchronize，可包括当时该设备的其他待完成工作；旧SHA与prepare通常已有同步操作，但未记录此处的pending队列，不据此认定存在积压。

源码定位：[worker.py](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/cluster/worker.py:217)、[keeper.py](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/serving/keeper.py:180)、[catalog.py](/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib/fluidckpt/cluster/catalog.py:22)。完整相对路径、原件与SHA见 [audit.json](audit.json)。

## 可区分解释与最小诊断

|解释|已有证据与缺口|区分观测/干预|
|---|---|---|
|每handler首操作的主机/runtime成本|两batch首操作明显更慢；旧数据没有分段或TID|输出TID、CPU与wall，拆begin-log/source-slice/target-slice/copy-submit/sync；必要时同handler预热后再计原操作|
|4/16MiB底层copy算法或引擎阈值|源码copy调用一致，HIP dispatch未知|同handler、相同source与预分配目标capacity，4→16→4；对照event窗口，必要时实际HIP trace确认API而不是据尺寸猜|
|目标不同大小的allocator/地址/first-touch影响|prepare分配在计时外，实际地址与storage offset未保存，copy内目标首触达仍不同|记录pointer、storage_offset、stride、device、contiguity；若差量落copy段，保持同16MiB容量目标只改变写入长度|
|begin日志、主机阻塞或sync等待|这些段都在record内，无分段证据|host wall与thread CPU定位非CPU等待的位置；它本身不能独立证明是OS调度或GPU原因|

首阶段保留原path和exact-size目标的native/diagnostic 4→16→16→4对照即可判断差量主要落在哪一段。若diagnostic仍出现首操作成本，后续最多两条同handler预热干预应由主根据分段决定；不预先扩大矩阵。

GPU event 窗可用于与主机墙钟对照，但 event.record自身和event前后主机提交间隙会改变观察。记录 event 初始化/record/elapsed读取在哪个边界、诊断总窗与各分段之和的残差；将event称设备事件窗口，不把它直接命名纯DMA时间。保留无仪表native controls约束observer效应。

旧反序重测排除了仅凭“小尺寸总是第一个测试”解释全部现象，但它保留了fresh handler、exact-size allocation、4MiB partial-source与16MiB full-source这组共变因素，也没有记录底层dispatch或pointer。所以它不能排除上述路径/首操作因素。一次8MiB通过固定服务模型，只说明那条新请求的服务预测偏差小，不能据此认定4–16MiB物理性能平台。

## 验证与状态

执行：`python3 codex_coding/src/v0.1.23.3/task2/local_slope/path/audit.py --output codex_coding/results/v0.1.23.3/task2/local_slope/path_audit_r01`。六原计划/记录的source一致性、模式、worker、chunk、记录角色及index不进入copy方法的AST核对全部通过。脚本只读原件，输出原件SHA和账目，没有CUDA执行、生成tensor、拟合参数或修改产品。

初次本地审计脚本把ROOT的parents写为7，读源码时FileNotFoundError，尚未输出任何结果；改为6后通过。本地默认python没有torch，未安装额外环境，也未进行替代tensor模拟。未知项是实际HIP API/设备拷贝时长/地址/每handler运行库成本；由实机诊断填充。
