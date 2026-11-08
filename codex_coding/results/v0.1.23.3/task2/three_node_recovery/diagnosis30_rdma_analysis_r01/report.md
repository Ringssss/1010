# node28/30 NCCL Socket回落：恢复现成平台插件配置

当前r01两端日志准确链路为：未找到默认librccl-net.so，改内部network plugin；请求=shca_0:1后NET/IB无device，最终Using network Socket。全1MiB payload SHA通过只认证数据功能，不能升级RDMA。r01的dump解析失败另由A/C修复，不能与传输选择混为同一个原因。

历史真成功原件topology_alignment/probe_startup_empty_r01的child12142明确记录env选librccl-net-shca.so、Plugin Path=/opt/rccl-rdma-sharp-plugins/lib/librccl-net-shca.so、NET/IB Using[0]shca_0:1/IB、Using network IBext_v8及channel NET/IBext_v8。对应ENV有NCCL_NET_PLUGIN=librccl-net-shca.so、NCCL_IB_HCA=shca_0、HSA_FORCE_FINE_GRAIN_PCIE=1。该empty_seed组本身non-GDR，已有别的旧GDR组也走同vendor插件，但不拿其GDR能力替代本次自有gdr0 policy。

28/30各一次当前精确容器CPU只读取证：host/boot/fullCID/StartedAt/image/tasklabel和PID namespace前后通过，vendor插件均752,144B、mode0755、SHA e0c3fd25c91cdb19c078b8350dd9daadded89d66600b0e5ed298840658af3af6，与旧成功原件完全一致；libshca.so两端SHA781c787953e5c57d178407b50223d61f3521ae7bcae436eefdbe01a4c48f4515也同旧运行，libibverbs.so.1两端均a57430a6ab66c3fcbbfde8b5e4fed0232016207fe017a49e88b83b4091e631c9。基底LD_LIBRARY_PATH已有插件目录，默认basename路径librccl-net.so确实没有，当前基底env未显式选vendor插件/fine-grain；实际r01启动参数和日志也没有该显式配置。不能说vendor RDMA库未安装或core库搜索路径缺失。

最小可审的兼容配置恢复为父指定NCCL_NET_PLUGIN=librccl-net-shca.so、HSA_FORCE_FINE_GRAIN_PCIE=1，并显式NCCL_NET=IBext_v8使其他network不能静默回落。这是恢复现成平台配置加严格传输要求，不是性能单变量归因。HCA名字shca_0已在历史真NET/IB日志实际出现，当前sysfs同名不能说alias错；vendor插件是否接受当前=shca_0:1 selector尚未本轮实测，父可直接复用旧shca_0表达并要求实际selected port回传1。

下一次功能运行仍须每rank出现本PID/rank的vendor Plugin Path、Using IBext_v8、实际shca_0:1/IB、cross NET/IBext_v8 channel与nNodes>1、全1MiB SHA，以及准确owned input XML载入和dump selectedBDF/CPU/chain校验、cleanup和前后保护。若这些没出现，诚实保留Socket-only或启动失败，不冒认IB/GDR/带宽。自有XML及默认mapping保持，gdr0不改。

本调查不导入Torch、不load插件/GPU库、不verbs查询或通信，也不执行新的GPU实验。现成插件存在且同SHA已够准备下一兼容组，停止扩大hardware/library调查。全部原件/SHA与准确ENV见summary.json，父/A协调r02命令，C独立审。
