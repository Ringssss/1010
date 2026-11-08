# node28 首次 fresh init 独立验收

原件一致性和窄保护检查通过，GPU初始化失败。入口 `review_runtime/review28.py --run .../probe_node28_init_r01 --after .../admission28_guard_after_init_r01 --out .../review_runtime_node28_init_r01`；输入 SHA 和冻结检查源码见 `summary.json`、`source.py`。

本次原 `927f543e...` gate、OMP/MKL各1、GPU2 selected、privateIPC配置的新child，在Torch导入2.798160722s后进入set_device；没有set_device_exit、HIP BDF或HIPfree事件。总15.114971586s含回收，不能描述为set_device完整等待15s。child13/start485279697的argv、PGID/SID与注册一致，精确TERM后absent，pending组为空。init_deadline_passed=true来自init模式没有独立分段deadline，不能推翻timed_out=true和runtime_passed=false。

当轮before/after同boot和新容器完整ID/StartedAt；旧两容器和13个owner/worker/keeper保持exact身份；六份catalog哈希等06:17新基准、共1098副本均lease0，事务为空。CPU-only memory_owner62788由keeper_process_discovery核实，未误要求它出现在KFD集合。此处认证目录与进程连续，没有重读完整权重内容，也不认证27/29保护。

目前本地取得registration、source、owner、process、events、summary和child.log七份原件；超时未生成child.result。child.log为32B，SHA `180dd7845395720f807239b1255d26938b30ba1fd151e567feb71213f31a8b32`，内容为 `Could not open /var/log/hylog/.`，不构成日志目录缺失或唯一根因证据。初审错误地以默认忽略*.log的rg列表判断该文件未归档，现已通过直接exists/stat读取更正；r02补全log与owner/event检查。copy、自有XML和性能均未发生。28与旧30观察是跨节点的共同配置失败现象，不能直接归因为同一根因；后续可在28同容器只改一个明确环境候选，保持原源码、GPU/BDF、visibility、线程环境和deadline。
