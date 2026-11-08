# 三路径实际校准与查询开销对照

`costcal_runtime_r02` 已执行完成，session 64.956秒含启动、mesh、预热、A/B、校准、组合与九 STOP；这不是恢复时延。唯一六worker拓扑、原三容器与原输入XML复用。原 r01 monitor 事务误拒失败保持，16MiB 自有SSD残留经精确 CID/boot/挂载/size/全SHA核查后由主单文件删除；见 `../execution_fix_r02/failure_finalizer.md` 和 `../execution_ssd_cleanup_r01/summary.json`。

结果是查询库解析开销已实机定位并消除；三个路径的正式联合请求模型未能识别，因此正式 prediction/accuracy/排序资格为 null。指定组合已实际执行和逐字节验证，不把缺失模型填成预测成功。

## 查询定位与同内容恢复对照

六worker无payload库存，各模式两次。legacy controller中位数60.576ms，cached 6.190ms；库解析/加载最大worker分段下降55.290ms，精确静态身份保持，动态项继续fresh，预登记归因门通过。缓存仅已计费的库路径/CDLL；HIP BDF、NUMA/HCA、PID/boot/epoch、cgroup与native库存不缓存。

36MiB组合固定为 localHBM w4→4、remoteDRAM w2→4、SSD w4→5各12MiB；w4先接后发。先cached，后legacy各一次，相同source identity/generation/epoch/offset/字节/worker/path，目标ID独立。两次三op共72MiB全SHA通过。

| 同controller时间段(ms) | cached | legacy | legacy−cached |
|---|---:|---:|---:|
| 完整请求→filled | 42.207 | 156.026 | 113.819 |
| 完整请求→published | 114.392 | 228.260 | 113.868 |
| 请求前置fresh库存 | 5.554 | 62.551 | 56.997 |
| 原生transfer内部库存 | 5.205 | 60.326 | 55.121 |
| prepare RPC | 3.730 | 4.163 | 0.433 |
| execute | 23.142 | 23.286 | 0.144 |
| 全SHA与发布 | 72.184 | 72.234 | 0.049 |
| 原生transfer→filled | 32.078 | 87.777 | 55.698 |
| 原生transfer→published | 104.263 | 160.010 | 55.747 |

完整请求起点在必要fresh库存、预算gate、构plan之前；它还包含预测/journal落盘及SSD状态观测。原生transfer窗单列以避免和上一轮旧窗口混用。两个库存差量解释完整filled差量的98.51%；execute基本一致。完整filled下降72.949%，published下降49.885%。固定顺序、每模式一次，结论限于该同workflow实测，没有统计显著或排除顺序效应的认证。

## 校准与模型未识别

真实逻辑请求为9个原训练（3path×3config）、3个仅localHBM的预登记反序重测、2个组合；warm原训练72MiB、补测24MiB、组合72MiB，另两次source bootstrap32MiB，共200MiB transfer全SHA。四op batch是一个请求，不能把其四op算四个独立重复；fixture生成与控制查询另计。九拟合行中local三行是两次均值，全部原始点保留；cold实测数为0。

| path | 拟合target小/大/batch(ms) | raw beta(s/B) | raw op(ms) | raw request(ms) |
|---|---:|---:|---:|---:|
| remoteDRAM 2→4 | 0.838 / 2.074 / 1.710 | 9.82076e−11 | 0.291 | 0.135 |
| localHBM 4→4（两次均值） | 0.381 / 0.277 / 0.676 | −8.26454e−12 | 0.098 | 0.318 |
| SSD 4→5 | 7.571 / 21.272 / 11.843 | 1.08887e−9 | 1.424 | 1.580 |

以上schema2 raw项是冻结观测输入的离线算术；三条路径的raw op/request均非负，本轮无此类clamp。local byte斜率仍负，原 `fit_request_model` 拒绝整体拟合，未补第三次、挑点、改变尺寸或为了达标生成正beta。原 `fit_path` 单op方法也拒绝local；其余两路径给出remoteDRAM有效service10.187GB/s、SSD有效service0.918GB/s和单op固定项0.371/2.954ms。它们包含执行原语/HTTP/staging/同步成本，不能命名物理链路带宽或完整三路径预测。

model冻结文件 `frozen_model.json` SHA `7185000f98498088962c89cf72c3379d7f3416362b2e9b832af6d671349c9ab8`，内容明确model=null。冻结与确定性plan/prediction=null均早于正式组合request_begin；必要fresh gate/plan仍在完整请求窗，并核与预登记相同。因此本轮保留了独立验证流程，但没有可计算的正式预测误差或排序验收。

local 4→16MiB两次均值差量约0.104ms，相当于组合controller execute23.142ms的0.45%量级，仅是尺度比较，不是预测误差。后续若确实影响决策，可考虑local固定项或分段服务结构；本轮不增加模型、GPU样本或调参搜索。

## 资源和退出

三node（28/30/27）supervisor DRAM采样峰为3,492,528,128 / 4,031,156,224 / 4,381,360,128 B，均低于6GiB且含runtime/共享cgroup。同HIP绝对 `total-free` 聚合采样上界1,124,073,472 / 1,281,359,872 / 1,826,619,392 B，均低于2GiB；没有sysfs-HIP相减。postwarm与每请求保守预留64MiB新应用，完整瞬时峰值仍unknown。

最终VALID catalog 0、六worker HBM/DRAM/SSD used全0、事务全空、三SSD服务文件数0。九STOP及九supervisor rc0；九child均planned STOP→TERM/-15，原exact helper确认absent/group空，无KILL。三collection通过原XML、旧owner身份、旧容器与quiet任务容器保护检查。仅新session底本/目标退出，原实验/产品/XML保持。

[summary.json](summary.json) 包含完整数字、13条SHA引用、每path raw/clamp、计数、同内容检查、冻结顺序及资源/退出证据；[summarize.source.py](summarize.source.py) 为只读解析入口。正式独立资格由review另行给出。
