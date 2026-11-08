# GPU4 C1真实账本与冻结独立验收

原始请求/数学模型228项、物理资源/清理173项通过。合并资格 `qualification.json` 的 `review_scope=C1_raw_resource_model`、`CPU_REPLAY_ONLY=false`，校准bundle引用与冻结模型逐字节一致，可作为C2前置独审引用；C2仍需新session及fresh物理准入。

唯一有效C1为 `execution_gpu4_actual_C1_r01`，六请求按L3→L1→L4→L2→L5→L3′完成，384MiB测量payload、9op、13实际copy/sync，另有96MiB source bootstrap。全部逐op源/目标SHA相等，原始copy/land/sync向量及偏移与计划一致，目标fresh分配无任务预写且每个目标一轮写入，真实HTTP handler birth保留。完整native execute父窗、原生record边界和controller各子窗独立核对，未用残差恒等式代替覆盖证明。

独立Fraction重算确认：仅前五点参与全部无约束OLS，L3′仅用于漂移；全部带符号系数、残差、两份留出预测、E及首五点外层中位残差与冻结结果一致。worker漂移38.411µs≤59.49735µs，service漂移29.850µs≤47.6102µs。E=83.030µs，由dummy logger变化尺度主导。

四项worker直接差量c=57.302µs、d=87.501µs、32b=0.879µs，未达到166.060/166.060/332.120µs固定尺度门；service的32b=-2.880µs保留。因此细项未识别，不能解释为物理带宽或固定sync费。预声明简单聚合 `a+bN` 满足自身残差及非负原系数门，被选作有限域粗迁移候选。外层只各加一次首五点中位残差：controller−worker=1.762735ms，filled−controller=6.166163ms。

| 事前留出 | service µs | worker µs | controller execute ms | full filled ms |
|---|---:|---:|---:|---:|
| V2：40+24+16MiB | 509.3031 | 610.6379 | 2.3733729 | 8.5395359 |
| V1：16+16+16MiB | 415.7421 | 513.8569 | 2.2765919 | 8.4427549 |

以上尚非独立预测准确率，K=3为明示外推；published/live预测null，decision_ready=false。不得用留出实测控制残差回填，固定绝对门不随E放宽。

资源原件显示DRAM观测最大1,918,480,384B、同HIP HBM观测最大675,282,944B；pre/postmesh、逐请求及结束门满足原6GiB/2GiB上限及256MiB安全余量。测量期间按冻结session调用 `monitor=None` 避免并发HIP查询，supervisor保留cgroup采样；连续HBM峰值未知。原4个容器/0个登记旧owner、原XML及全部157项部署资产SHA保持；最终Catalog/used/tx/SSD空，唯一supervisor rc0，TERM后child−15，PID组为空，collector无追加信号。

`review_gpu4_resources_C1_r01/failure.json` 保留审查器误要求可选budget_monitor文件的失败；修正仅限review代码，使用冻结的pre/post/per-request/end资源证据后在新r02目录通过，未改变实验或门槛。
