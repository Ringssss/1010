# node27完整模型物理暂停局部脚本独审

结论：新局部runner与host脚本关键边界通过。仅本地AST函数提取和已存在CPU原件检查，GPU/SSH/Docker调用0；没有重跑先前35项套件。独立实际host build→runner validate互通及预算等6项检查通过，另核对已有16项local runner CPU契约通过且源码SHA一致，见 `qualification.json`。

发现的初版缺口是host未设置`warm_pairs=[]`，会被runner在任何RPC前拒绝；实现agent已明确补齐。当前host构造的真实局部计划通过runner验证。保留两keeper world2、node27 GPU4/5及TP2/basegpu4、全8卡visibility，helper role与remote mesh删去。原target DRAM192GiB/hard224GiB、HBM48GiB/card及原余量门逐字段保持，未增加成本参数或放宽预算。

runner只依次init本地mesh/空warm、完整捕获单映射全SHA、参考输出、release_live、before/after inventory、两rank物理暂停90%门和66个VALID DRAM区域lineage/准确字节持有检查；成功到此结束。无helper/cache/transfer/在线resume/pathcal/C0/留出，结果明确`online_cost_samples=0`、`online_cost_qualified=false`。全SHA在capture时完成；暂停后保留来源所有权证据，不重新导入同一IPC凭证。

host继承构造器只读取原two-node本地spec，随后self.specs仅node27；继承admission/deploy_plan/collect的迭代对象均为该限定集合。所有新start/HTTP inventory/controller/STOP命令显式node27。新host还拒绝非node27本地主机，避免SSH fallback。runner仅以新文件xb追加至原冻结核心138资产，上传有内容SHA和有界长度校验；已存在路径拒绝，旧运行不覆盖。host源码与追加runner SHA在本次审查引用中单列。

fresh admission须在三角色启动前通过，准确CID/StartedAt/boot_id、原冻结资产、pids.current=1均再次检查。成功/失败都按engine先于keeper发送绑定plan SHA的准确STOP，记录client退出并尝试原生完整artifact收集和最终pids1。guard失败、client非0或quiet失败均保留为失败；仅kill本地主机docker client不等于已回收GPU子进程，需依原准确supervisor与最终进程/容器证据验收。

尚无实际完整模型释放证据。本地局部暂停通过只认证当前TP2 incarnation的capture/park修复，不改变node30准入失败或在线成本模型资格；实际原件返回后应独审per-rank allocated drop、整卡free gain、SHA覆盖、DRAM保活、资源门、原guard/reap/quiet三层状态。
