# Debug 哲学

本文档总结了我们在 EpochServe/MPK 项目中形成的 debug 方法论。这些原则来自多轮深度 debug 实战，包括 persistent kernel 中 split_kv attention 的 4 个 bug、CUTLASS prefill GEMM epilogue 静默数据丢失 bug、以及 chunked prefill causal mask offset bug 的定位和修复。

---

## 一、核心原则

### 1. 控制变量：不猜机制，找变量

遇到 bug 不要猜"可能是 XX 机制的问题"，要找**变量**。

- 列出所有已知的 PASS 和 FAIL case
- 找到 PASS→FAIL 之间**唯一变化的那一个因素**
- 设计实验只改变那一个因素，其他全部固定

> 猜测是 O(n!) 的搜索空间，控制变量是 O(log n) 的二分。

### 2. Printf 让数据说话

直接输出地址、值、索引、路径 — 不靠脑补推理。

- 不要 "我觉得它应该走这条路径" → printf 确认它**实际**走了哪条
- 不要 "这两个地址应该一样" → printf 打出来对比
- 不要 "这个值应该是正确的" → printf 打出具体数值

> printf 是 O(1) 的验证，脑补是 O(∞) 的猜测。花 1 分钟加一行 printf 比花 1 小时读代码推理更有效。

### 3. 隔离实验：排除交叉污染

一次只验证一个假说，确保实验环境干净。

- 进程隔离：不同 test case 用独立进程（避免 GPU memory 残留）
- State 清理：每次跑实验前 `rm -f *.so && rm -f *.lock`
- Sequential 请求可能互相污染 → 单 request 测试先确认

### 4. 先确认路径，再看数据

代码中有 `if constexpr`、`#ifdef`、early return 等多条路径。不要假设实际走了哪条 — **printf 确认**。

- 在每个分支入口加一行 printf（限制 1 次），看哪个打出来了
- 确认了路径之后，再在该路径中加详细的数据 printf
- 这避免了"在错误的分支上花时间分析"

### 5. 二分法找边界

PASS→FAIL 的精确分界点是最有价值的信息。

- 已知 A PASS、B FAIL → 二分找到 A' 和 B' 使得 |A' - B'| = 1
- 分界点直接指向 bug 的触发条件
- 边界可以是：数值（plen=255 PASS / plen=257 FAIL）、配置（max_seq=480 / 512）、迭代次数等

### 6. 警惕自己引入的错误

Debug 脚本和测试配置本身可能有 bug。当结果不符合预期时：

- 先检查自己的实验设置是否正确
- 打印编译命令确认参数
- 和已知正确的脚本对比配置差异

> 如果你的"控制变量"实验本身有一个多余变量没控制住，所有结论都是错的。

---

## 二、方法论流程

```
Step 1: 复现 — 确认 bug 存在，建立最小复现 case
    ↓
Step 2: 矩阵 — 列出所有 PASS/FAIL case，找控制变量
    ↓
Step 3: 二分 — 找精确边界（哪个值/配置是 PASS→FAIL 的分界）
    ↓
Step 4: 路径 — printf 确认实际执行路径
    ↓
Step 5: 数据 — printf 打关键数据（地址、值、索引）
    ↓
Step 6: 对比 — PASS case 和 FAIL case 的 printf 输出逐行对比
    ↓
Step 7: 定位 — 找到第一个数据不一致的位置 → 对应的代码行
    ↓
Step 8: 修复 + 验证 — 修改代码，重跑全部 PASS/FAIL case 确认
```

---

## 三、常见陷阱

### 陷阱 1：错误的实验配置误导方向

**表现**：某个变量看起来是关键因素，但实际是 debug 脚本自身引入的差异。

**本轮案例**：debug 脚本设置 `BT = batch_requests * 8`（batch=4 时 BT=32），导致以为 "batch=4 会触发 bug"。实际 BT 应始终为 8，修正后 batch=4 完全正确。

**教训**：当实验结果和理论矛盾时，先怀疑实验设置，再怀疑代码。

### 陷阱 2：Sequential 请求间的 state 污染

**表现**：request 2+ 的结果异常，容易误判为"多请求交互 bug"。

**本轮案例**：5 个 sequential request 在同一个 kernel 跑，req 0 正确 req 1-4 错误。一开始以为是 sequential 污染，后来隔离实验（total_requests=1）确认 req 0 本身就错 — 是 split_kv 对所有 request 都有 bug，只是 req 0 恰好输出"看起来半通顺"的垃圾。

**教训**：先隔离单 request 确认，再讨论多 request 交互。

### 陷阱 3：Printf 计数器被消耗

**表现**：printf 只打了前 N 条就停了，遗漏了关键数据。

**本轮案例**：`static __device__ int counter` 限制 5 条输出，全被 req 0 的 decode 阶段消耗完，看不到 req 1 的数据。

**教训**：
- printf 条件要精确（如 `global_seq_len == 315 && kv_idx == 1`）
- 或改为单 request 运行，避免竞争计数器
- 计数器设大一点（20+）不会影响性能太多

### 陷阱 4：地址匹配不代表数据正确

**表现**：merge 读的地址和 attention 写的地址一致，但输出仍然错误。

**本轮案例**：attention 的 output store 地址和 merge 的 read 地址完全匹配。bug 不在 merge（我们之前修的 Bug #3 的位置），而在 attention 内部读 KV cache 时的 page 索引偏移。

**教训**：不要因为"地址对了"就停止排查。数据流是 KV read → attention compute → O write → merge read → final output，任何一步都可能出错。

---

## 四、案例：Split-KV Page Index Offset Bug（Bug #3 + Bug #4）

### 背景

EpochServe 的 persistent kernel 支持 split_kv attention：当 `max_seq > 256` 时，KV cache 被分成多个 chunk，每个 chunk 独立计算 partial attention，最后由 merge kernel 合并。

### Bug #3（已修，v0.1.16.14）

**现象**：batch=4 时 request 2+ 输出 garbage  
**根因**：`merge_splitkv.cuh` 用 local `token_idx` 读写 intermediate buffer，不匹配 attention 用 `first_token_pos` 写入的偏移  
**修复**：3 处 `token_idx` → `global_token_idx = token_idx + first_token_pos`

### Bug #4（本轮修复）

**现象**：max_seq=512 + prompt > 256 tokens → 输出 garbage（重复 token）  
**控制变量路径**：

```
max_seq=480 (1 chunk) → PASS    ← 无 split_kv
max_seq=512 (2 chunks) + plen=191 → PASS    ← chunk 1 无数据
max_seq=512 (2 chunks) + plen=314 → FAIL    ← chunk 1 有数据
```

**Printf 排查路径**：

```
1. [PATH-NORMAL] 确认走 TMA path（不是 fallback）
2. 地址对比：attention write addr = merge read addr（匹配）
3. LSE 值合理（非 0、非 NaN）
4. 审查 attention 代码：发现 page_indices 索引缺少 chunk offset
```

**根因**：`multitoken_paged_attention_hopper.cuh` 中 2 处 page_indices 索引在 kv_idx>0 时缺少 `kv_cache_offset / PAGE_SIZE` 偏移：

```cpp
// Bug 1: producer prefetch 后续 tile
// 原始: page_indices[(iter+1) * KV_TILE_SIZE / PAGE_SIZE]
// 修复: page_indices[kv_cache_offset/PAGE_SIZE + (iter+1) * KV_TILE_SIZE / PAGE_SIZE]

// Bug 2: KV cache 写入
// 原始: page_indices[first_kv_token_to_process / PAGE_SIZE]
// 修复: page_indices[(first_kv_token_to_process + kv_cache_offset) / PAGE_SIZE]
```

**为什么之前没暴露**：chunk 1 只在 prompt > 256 tokens 时才有多个 KV tile。之前所有测试 prompt < 256。第一个 tile 的索引 `page_indices[kv_cache_offset / PAGE_SIZE]`（line 532）是正确的，只有后续 tile 缺少 offset。

**走过的弯路**：
1. 误以为是 batch 大小的问题（实际是 BT 配置错误）
2. 误以为是 total_requests 导致的 sequential 污染（实际是 split_kv 对所有 request 都出错）
3. 误以为是 chunk boundary 256 的问题（实际边界在"chunk 1 有多个 tile"时触发）

**最终验证**：Prefix Cache（5 requests, max_seq=512）和 Preemption 均 PASS。

---

## 五、新增原则（v0.1.16.18 CUTLASS epilogue 静默数据丢失 bug 提炼）

### 7. 警惕"部分正确"的陷阱

完全错误（NaN、cosine=0、全 garbage）会立即暴露，但**部分正确**是最难抓的 bug：输出不崩溃、不报错，只是"值不太对"。

- 如果 abssum 是 HF 的约一半 → 不是精度问题，是结构性丢失
- 如果有符号 checksum 接近正确但 max_error 很大 → 正负抵消在掩盖问题
- 如果 token 0 正确但 token 16 错误 → 不是"随机噪声"，是某个维度的系统性截断

**本轮案例**：gate_up GEMM 的 epilogue OOB predicate 把 features [64, 128) 的写入静默跳过。输出不是 0 也不是 NaN，而是正确值的约 50%（前 64 features 正确，后 64 未写入）。cksum64（有符号和，只看 64/4096 features）无法检测，abssum（无符号，全 features）才暴露了问题。

**教训**：
- 用**全量**无符号 metric（abssum over all features）作为主检验，不要依赖有符号 checksum 或部分采样
- 当 metric 差异呈现"整数倍"关系（差一半、差 1/4）时，高度怀疑是维度截断或 stride 跳步
- "不崩溃"不代表"正确"——静默数据丢失比 segfault 更危险

### 8. 验证"不受影响"的假设

当发现某些 case PASS、某些 case FAIL 时，我们会建立假说解释为什么 PASS case "不受影响"。但这个假说本身需要验证——"碰巧不触发"和"机制上不可能触发"是完全不同的。

- **碰巧不触发**：Token 0 正确是因为它的 abssum 主要由前 64 features 支撑，后 64 features 的缺失在全量 metric 中不够显著
- **机制上不可能触发**：O_proj (OUTPUT_SIZE=64 ≤ BATCH_SIZE=64) 在数学上不会触发 OOB predicate

**本轮案例**：我们一度认为 "token 0 正确 → offset=0 不受 stride bug 影响"。这个直觉方向大致正确（offset=0 的特殊性确实存在），但如果没有进一步追问"那 token 16 为什么恰好差一半而不是差某个其他比例"，就不会想到去审查 OOB predicate。

**教训**：
- 对 PASS case，问"它为什么能 PASS"——如果答案是"碰巧"，那 PASS 不能排除 bug 存在
- 对 FAIL case，问"差异的具体模式是什么"——"差一半"比"差一点"包含更多信息
- 不要用 PASS case 来**排除**假说，要用 FAIL case 来**构建**假说

---

## 六、新增实践（v0.1.16.19 Chunked Prefill causal mask bug 提炼）

### 9. Printf 必须携带足够的 Context

在 persistent kernel 这种多 iteration、多 layer、多 task 并发的环境中，一行 printf 如果不包含 iteration number、layer index、task position，就无法唯一确定它来自哪里。缺少 context 的 printf 比没有 printf 更危险——它给出"看似有道理但实际误导"的数据。

**本轮案例**：`[PIPE]` 输出中 O_proj 的行出现在 ATTN 行之前 → 我判断为"O_proj 在 attention 之前执行 = race condition"。实际上这些行来自**不同 layer**——Layer N 的 O_proj 和 Layer M 的 attention 交错打印是完全正常的。因为没有 layer 信息，我无法区分"同一 layer 内的 race"和"不同 layer 间的正常交错"，直接走偏了方向。

**教训**：
- Printf 至少包含：`iteration_num`（或用数据 fingerprint 区分）、`layer_id`（或 ptr 地址）、`task_type`
- 如果 printf 不包含足够 context，先加 context 再解读数据——否则"看到了数据但得出了错误结论"比"没看到数据"更浪费时间
- 在 persistent kernel 中尤其重要：同一个函数可能被几百个不同 task 调用，每次的语义完全不同

### 10. 区分"编译时路径"和"运行时路径"

GPU kernel 大量使用 `if constexpr`、`#ifdef`、template specialization 来在编译时选择代码路径。当 printf 没有输出时，存在两种完全不同的原因：

- **运行时路径不满足**：代码被编译了，但 `if (condition)` 不满足 → 检查条件为什么不成立
- **编译时路径被排除**：代码根本没被编译进 binary → 去找**另一个分支**的实际代码在哪里

**本轮案例**：在 hopper attention 的 output store loop 后加 printf → 一直没输出。排查了很久才发现 `if constexpr (S_TOTAL_OFFSET > MAX_SMEM)` 在编译时就把 prefill 路由到了 `prefill_attention_hopper_impl` 的 fallback 路径，然后 `return`。我的 printf 在 return 之后——永远不可达。不是"运行时没走到"，而是"编译时就不存在"。

**教训**：
- Printf 不出现时，**第一反应应该是怀疑代码路径**，而不是怀疑 printf 本身
- 在 CUDA/CUTLASS 代码中，先搜 `if constexpr`、`#ifdef`、`static_assert` 确认你加 printf 的分支是否被编译
- 如果不确定，在函数**入口**（所有分支共享的位置）加 printf 确认函数是否被调用，再逐步缩小到具体分支

### 11. 同一现象的多个自洽解释——设计区分实验

当一个观察到的现象可以被多个假说同时解释时，不要急于选择"最直觉"的那个——而是设计一个能**区分**这些假说的实验。

**本轮案例**："同一个 ptr 地址出现了两个不同的值"这个现象，我给了三个解释：
1. Race condition（两个 iteration 竞争写同一 buffer）
2. 不同 layer 正常交错（同一 iteration 内不同 layer 依次写同一 buffer）
3. Stale data（前一个 iteration 的残留被后一个读到）

三个解释都能自洽地解释"同一 ptr 不同值"。我最初选了 (1) 就走偏了。

**教训**：
- 列出所有自洽解释，然后问"什么实验能区分它们"
- 通常只需要加一个维度的信息：加 layer → 区分 (1) 和 (2)；加 iteration → 区分 (2) 和 (3)
- 这和"控制变量"原则是一致的，但应用场景是**对 diagnostic 数据的解读**，而不仅是对 bug 的排查
- 当你发现自己在"选择相信哪个解释"时，停下来——这说明你需要更多数据，而不是更多推理

---

## 七、新增原则（v0.1.19.17 InfiniLM paged packed layout 条件修复提炼）

### 12. 预期数据驱动的修改验证

不做没有预期的修改，不做没有验证的推测。每次修改都必须经过"改前确认 → 写预期 → 改代码 → 对照预期"四步闭环。

**方法论流程**：

```
Step 1: Printf 确认认知（改前验证）
    ↓  → 不符合 → 停下，修正心智模型，不要继续改
Step 2: 写出预期数据（具体数值，可逐行对照）
    ↓
Step 3: 改代码（minimal，只改 Step 1 确认的问题点）
    ↓
Step 4: 跑，逐行对照预期数据
    ↓  → 不匹配 → 回到 Step 1，不是继续改代码
Step 5: 全部匹配 → 进入下一阶段
```

**本轮案例**：修正 `forward_paged_()` 中 `batch_size > 1` 条件时，直接基于代码阅读推测"slot 0 的 `cached_seq_len` 被覆盖为 8"，然后盲目加了 guard + state restore。结果引入新 bug（`positions_size` mismatch → throw）。

正确做法（回溯后）：
1. **改前 printf**：在 `build_paged_attention_inputs_` 中打出 `cached_seq_len`，确认确实是从 2 被覆盖为 8
2. **写预期**：修改后应看到 `cached_seq_len=2 positions_size=2 slot=0 seq_len_arg=2`
3. **改代码**
4. **对照**：实际输出和预期逐行匹配 → 确认修复正确

**走偏的错误模式**：
- 猜 → 改 → 跑 → crash → 猜另一个原因 → 改 → 跑 → 新 crash → ...（O(n!) 搜索）
- 每次"改"都是基于上一次 crash 的推测，但从未验证推测本身是否正确

**核心区别**：
- Printf 不是用来"看看怎么回事"，而是用来**验证你的预言**
- 预期数据不是模糊描述（"应该正确"），而是**具体数值**（`cached_seq_len=2`）
- 不匹配时的动作不是"继续改代码"，而是**回头修正认知**

**教训**：
- 基于代码阅读的推测正确率远低于 printf 一行的确认
- "连环盲修"是最浪费时间的 debug 模式——每次修改引入新问题，每次新问题的定位成本比上一次更高
- 写预期数据的过程本身就是验证理解的过程——如果写不出具体数值，说明理解还不够，不该动手改
