# S5正反顺序控制：独立事实复核

开发编号v0.1.23.3/task2；只读复核s5_r01/s5_r02，新GPU测量0，复用原4次恢复。原结果与冻结代码未改动。

两轮都通过独立控制检查：归一化目标phase ID后，6项op集合（source ID/epoch、TP、范围、generation、path与resources）严格相同；每轮case输入的replicas、budget、format、profile和目标静态tensor内容相同。排除的目标字段仅为每次恢复标识、IPC导出index与t_export。每轮两case的完整SHA结果一致，4/4内容、输出验收通过。每case总70329718112字节，HBM来源8589934592字节、DRAM来源61739783520字节。

实际4个worker的记录序、source/target角色和每op字节均与对应计划匹配。source0从[0,2,3]变为[0,3,2]；source1保持[1,4,5]，target2/3保持[0,1,2]/[3,4,5]。因此改变的是source0把rank1的首段HBM op3提前至rank0长DRAM op2之前的串行依赖，未改变来源/布局/数据。

| 轮次与实际先后 | target_major T_ready(s) | interleaved T_ready(s) | target_major execute(s) | interleaved execute(s) |
| --- | ---: | ---: | ---: | ---: |
| r01：major→interleaved | 4.461190392 | 2.645741278 | 3.852872509 | 2.210528380 |
| r02：interleaved→major | 3.672967243 | 2.867577637 | 3.232560931 | 2.206665278 |

各目标rank事件均来自同host m03r2n09；以下窗口只用目标本地monotonic，不减源目标跨机时钟。

| 轮次/顺序 | rank0复制窗口(s) | rank0 copy→publish(s) | rank1复制窗口(s) | rank1 copy→publish(s) |
| --- | ---: | ---: | ---: | ---: |
| r01 target_major | 1.682631 | 2.171555 | 3.848891 | 0.004887 |
| r01 interleaved | 2.206521 | 0.004240 | 2.198201 | 0.012517 |
| r02 interleaved | 2.202376 | 0.003591 | 2.196496 | 0.009845 |
| r02 target_major | 1.365895 | 1.867058 | 3.226355 | 0.006626 |

最短因果链：source0长DRAM op2挡在rank1首段HBM op3之前→rank1接收及source1后续发送链等待→将op3提前后两rank长尾能并进→最慢rank窗口缩短，先结束rank0等待共同complete/publish的长尾消失。worker.py:233的逐op串行与control.py:153的all-execute后complete支持这一解释。rank1 op3本地elapsed major为2.091/1.469s，interleaved约0.205/0.206s；source1 op4 major为1.732/1.424s，interleaved约0.134s。elapsed包含同步与配对等待，不能写成纯传输速度。

建议共同表述：**正反执行顺序的两个探索配对均支持，在当前同布局、同来源、同操作的串行worker执行器中，提前发送另一rank的缓存可解除长串行依赖并缩短权重就绪窗口。** 两轮各自T_ready降幅为40.69%与21.93%，保留配对值，不合成稳定排名。反向仍有收益，说明观察不只依赖交错方案最后运行；它没有排除全部热状态、时间漂移或环境效应。两轮major execute存在约0.62s差异，interleaved执行窗口接近；pre_execute和arrival skew也变化，不能把全部T_ready差值精确归因于重排。

范围：物理NIC/fabric共享门槛仍not_measured；本实验是同fastest选源下的人工操作排序控制，不证明placement、resource_lp/MILP或联合算法优势，也不比较Janus/BlitzScale/ServerlessLLM原版整系统。输入指纹、逐worker验证、原始计时与不成立的结论见summary.json。
