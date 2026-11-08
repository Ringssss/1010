# 第一阶段结果与可区分的机制

本次原路径没有稳定复现旧4MiB慢于16MiB的负斜率。顺序4→16为0.253934→0.238223ms，差−0.015711ms；反序16→4为1.037124→0.172842ms，差+0.864282ms。完整四点均值4=0.213388ms、16=0.637673ms，大−小+0.424285ms。1.037124ms长点原件保留，不删除或以另一次替换；该点没有分段仪表，因此其具体慢段无法定位。旧训练负差仍为历史事实，本会话不同结果不改写它。

诊断模式四点为4=0.352955/0.301664ms、16=0.290974/0.293464ms，均值大−小−0.035091ms。最大分段差来自event_begin_record −0.018900ms，source_slice仅−0.004755ms，begin_log −0.003865ms，copy_submit −0.001250ms，device_sync −0.002530ms。分段thread CPU与wall近似，说明这些仪表窗口多数时间在线程执行，而不是明显离CPU等待；不能用这几个窗口计算HBM物理带宽。

首source slice约62–67us，后target slice约8us，支持进一步检验“每新handler首次Tensor索引/派发固定成本”的假说，但两尺寸source差约4.8us，不足以把旧约0.1ms差量归因给source尺寸分支。官方PyTorch源码的fullslice/partial同路线证据由path审计提供，本执行结果不能支持“fullsource直接返回self从而绕过slice”说法。

GPU event4=0.106880/0.103840ms，16=0.094880/0.100800ms，均值差−0.007520ms。event夹窗包含copy提交与host间隙，copy_submit CPU约70us、end_record CPU约30us；这些event不是纯copy kernel时间，当前不能认证设备尺寸负斜率。diagnostic另有prepare event预初始化+device sync且展开land分支、计时器开销，native和diagnostic分块顺序还混入漂移。诊断后metadata0.374595–0.436086ms处在HTTP/controller窗中，worker execute/service窗外，不命名controller dispatch。

native/diagnostic的execute thread name依次Thread-27/37/47/57与67/77/87/97，OS TID各不同，thread ident全部相同表示标识复用。prepare是相邻独立线程26/36等。所有source指针恒定，target按4/16MiB各复用同指针；source与全部target均contiguous、stride1，256/4096/2MiB对齐。两个native16MiB点地址与prepare allocated/reserved内存完全相同却相差约0.799ms，所以静态地址或分配类别本身不能唯一解释长点。目标大小仍影响分配类及reserved字节，尚无控制目标容量的干预，不作其无影响的断言。

八请求与16MiB bootstrap共96MiB全部字节SHA通过。会话49.802712s含启动/mesh/请求/STOP，不是restore latency；六GPU、九supervisor返回0，三node collection通过，final VALID replicas/used/transactions均空，三SSD files=0。精确资源与保护资格由独审另行认定。

本阶段结论是host runtime及首次调用成本需要与byte-cost分开，当前单op数据不支持恢复正带宽拟合。旧有限域固定service模型和参数保持，根因未认证，布局排序未认证。后续若主根据本阶段批准，只对首次source索引做同handler预热干预4/16各一次，分别记新增前置成本；不以额外测量挑选正beta，也不默认重复或扫边界。

数值来源为本目录summary.json引用的八条原始请求、execution_actual_r01/summary.json及measure/final_inventory.json；解析命令见本目录report.md对应analyse.py。CPU回放只验证生命周期/字段/CPU字节复制，其clock/event不用于以上数值。
