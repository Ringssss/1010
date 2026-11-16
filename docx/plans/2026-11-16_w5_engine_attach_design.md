# W5: attaching FluidPool-restored weights to a real SGLang engine

Date: 2026-11-16. This is the design for workstream W5 of `2026-11-16_fluidpool_overview_and_next_plan.md` (§5.5).

Status:
- **Code written.** CPU unit check passes.
- **Nothing run on GPUs yet.** The cluster is busy with `formal_r400_p1b`.

## 1. Goal

Measure activation the way a user sees it: **time to first token** and **SLO attainment** in a
real SGLang engine, with open-loop timestamped arrivals.

Every system is compared on the same attach path. Only the way the bytes reached HBM differs:
- FluidPool: multi-source flow over the cluster.
- Janus-style: local DRAM copy.
- SGLang-style: disk.

## 2. Mechanism chosen

### 2.1 The three candidates

| | Mechanism | Verdict |
|---|---|---|
| (a) | SGLang Weight Cache IPC loader (`IpcModelLoader`) fed by a FluidPool daemon speaking its protocol | **Not possible on this image.** The installed SGLang 0.5.12 (dtk2604) has no `srt/weight_cache/` package. It exists only in the frozen reference `lib_cite/sglang` @70f0b73 (`weight_cache/ipc_loader.py`). It is also zero-copy over *post-processed* parameters exported by a daemon that loaded the model itself. FluidPool holds the checkpoint's raw bytes, not SGLang's fused or sharded parameters. |
| (b) | Engine started with `--load-format dummy`, then `update_weights_from_tensor` with a custom weight loader | **Chosen.** Supported by the installed engine with no patch. |
| (c) | A new model-loader plugin | More invasive (load-time only). It cannot re-activate a running engine. |

### 2.2 How (b) works

1. Start the engine once with
   `--load-format dummy --custom-weight-loader fluidckpt.serve.sglang_attach.load_from_noded`.
   - The runtime is warm: HIP context, KV pool, CUDA graphs.
   - The parameters hold random values.
2. Restore the model with `Pool.restore(model, node, [card], release=False, use_arena=False)`.
   - noded keeps the job buffer: raw safetensors files laid out by `Pool.layout`.
3. Write the attach descriptor with `sglang_attach.describe_job(pool, model, node, cards, job)`. It contains:
   - per-card HIP IPC handles of the job buffers (`noded.op_job_export`);
   - the file layout;
   - the manifest's tensor table (name, file, offset, nbytes, dtype, shape).
4. Attach with `Engine.update_weights_from_tensor([("fluidpool:<descriptor>", torch.zeros(1))], load_format=LOADER)`.
   Over HTTP, use `POST /update_weights_from_tensor` with `serialized_named_tensors` built by
   `MultiprocessingSerializer.serialize(..., output_str=True)`.
   - Each TP worker reaches `ModelRunner.update_weights_from_tensor`, which calls our function through `dynamic_import(load_format)`.
5. `load_from_noded(model, named_tensors)` then, inside the worker:
   - imports the IPC buffers (`torch.UntypedStorage._new_shared_cuda`, the same call proven in `pool/ipc_attach_bench.py`);
   - builds one zero-copy view per checkpoint tensor;
   - runs `model.load_weights(views)`. This is SGLang's own name mapping, QKV/gate-up fusion, tied-embedding handling and TP slicing.
6. The parameters receive an in-place D2D copy:
   - same card at HBM speed;
   - over xGMI when a file sits on another card of the node (TP > 1).

   Because the parameter addresses do not change, captured CUDA graphs stay valid. The IPC
   mappings are dropped right after, so noded can `job_release` the buffer (copy semantics).

### 2.3 Why the copy is acceptable

- It runs at HBM bandwidth: ~15 GiB for 9g-8b means tens of ms. It is reported separately as `attach_s`.
- It keeps one raw copy valid for every TP layout and engine version. That is the property FluidPool's cluster-wide keep-alive relies on.
- Zero-copy aliasing would need FluidPool to store engine-specific post-processed shards.
  - Prior art for that is `main_lib/fluidckpt/serving/sglang_hook.py` plus the keeper. It captures post-processed storages per TP rank and covers the GLM/MLA case.
  - It is kept as an option for quantized models, not as the default.

## 3. Code entry points

FluidPool side (this repo):
- `main_lib/fluidckpt/serve/sglang_attach.py:47`: `describe_job`, the coordinator side.
- `main_lib/fluidckpt/serve/sglang_attach.py:83`: `import_buffers`, the IPC import. It remaps the device ordinal via `device_of`; identity when the engine uses `base_gpu_id`.
- `main_lib/fluidckpt/serve/sglang_attach.py:104`: `iter_tensors`, the views from layout plus the tensor table.
- `main_lib/fluidckpt/serve/sglang_attach.py:114`: `load_from_noded`, the custom weight loader. It logs `ipc_import_s` and `load_weights_s` to the descriptor's `log`.
- `main_lib/fluidckpt/live/noded.py:1717`: `op_job_export`. Also `:1704` `op_job_locators`, `:1786` `op_job_release`, `:588` `op_arena`.
- `main_lib/fluidckpt/live/pool.py:702`: `layout`. Also `:1166` `restore`.

Installed SGLang (`/usr/local/lib/python3.10/dist-packages/sglang/srt/`):
- `model_executor/model_runner.py:2102`: `update_weights_from_tensor`.
  - `:2125` is the `dynamic_import(load_format)` dispatch.
  - `:3691` is `_unwrap_tensor` (moves our 1-element tensor to the device).
- `entrypoints/engine.py:1035`: `Engine.update_weights_from_tensor`.
- `entrypoints/http_server.py:1167`: the HTTP route.
- `managers/io_struct.py:1466`: `UpdateWeightsFromTensorReqInput`.
- `server_args.py:887`: `custom_weight_loader`.

Scripts (`codex_coding/src/v0.1.23.3/task2/e2e/`):
- `attach_unit_check.py`: CPU only. A synthetic two-file safetensors checkpoint goes through the real `build_manifest` and `Pool.layout` placement on 1 and 2 "cards", then `iter_tensors`. **Passed: 9/9 tensors bit-exact** (bf16/f32/f16/i64).
- `attach_e2e.py`: hardware correctness and TTFT breakdown. Steps:
  - a reference engine loads from disk;
  - a dummy engine is checked to produce different output;
  - per rep: restore → describe → attach → `job_release` → 1-token generate → greedy output, compared to the reference.
- `replay_ttft.py`: skeleton for open-loop replay.
  - One HTTP server per model on its own card, with dummy weights and our loader.
  - At most `--resident` models hold valid weights at a time (LRU).
  - A miss triggers restore plus attach.
  - Arrivals come from `cluster-trace-v2026-GenAI/lora_request_trace.csv`, scaled by `--speedup`.
  - Reports TTFT p50/p99 and SLO attainment, for `--system fluidpool | local_dram | disk`.

## 4. Risks and open points (to verify on hardware)

1. **IPC across processes and containers.**
   - The engine must run in the same container as noded (`zjn-fluid-pool-<nn>`) on the restore node.
   - torch's CUDA-IPC ref-counter file lives in that container's `/dev/shm`.
   - `_new_shared_cuda` on DTK/HIP was proven only node27-local in `ipc_attach_bench.py`, mapping 1.9 ms per card.
2. **Device ordinal.**
   - The handle carries noded's ordinal, which is the physical card.
   - Start the engine with `--base-gpu-id <card>`. Do not narrow `HIP_VISIBLE_DEVICES`, or set `desc["device_of"]`.
3. **Memory.**
   - A 64 GiB card cannot hold noded's 48 GiB arena plus an engine.
   - The scripts release that card's arena (`op_arena(card, 0)`) and restore with `use_arena=False` into fresh memory. Direct writes into fresh memory run at 45–60 GB/s instead of 150–190 GB/s, so report which was used.
   - Choose engine `--mem-fraction-static` ≤ 0.45 with the restore buffer still alive during the attach.
4. **Layout and post-processing.**
   - `load_weights` does fusion and TP slicing. Unquantized linear post-processing is a no-op on this GPU (`unquant.py:160` acts only on CPU AMX).
   - Quantized checkpoints need `process_weights_after_loading` after the copy. `post_process()` is a best effort and untested. **GLM-5.3 fp8 is out of scope**: it does not start in SGLang on gfx936 at all (`docx/articles/2026-09-27_glm53_startup_vmfault_forensics.md`).
5. **TP > 1.** Each rank reads full tensors, possibly from peer cards over xGMI, and keeps only its shard. This is correct but reads `tp_size` × the bytes. Measure it on qwen36-35b TP2 after the TP1 models pass.
6. **Release/resume is a no-op.** `torch_memory_saver` is missing on this image, so an engine cannot give HBM back between activations. In the replay, "evicted" means the weights are marked invalid while the engine's memory stays allocated. State this in the paper: engine start-up and KV memory are outside the measured path for all systems alike.
7. **Tied embeddings and buffers.** Qwen3-0.6B ties `lm_head`; `load_weights` skips it. RoPE caches are computed at init, so the dummy engine already has them.
8. **`_unwrap_tensor`** moves the 1-element tensor to the device in every worker (negligible). Over HTTP, the CPU tensor travels by ForkingPickler shared memory. The client must stay alive until the call returns, which the scripts guarantee.

## 5. Commands to run on hardware after `formal_r400_p1b`

Daemons first (`--arena-gib 48` is fine; the scripts release the engine card's arena):

```sh
cd /home/zhujianian/zhujianian/fluidckpt_zjn
R=$PWD/codex_coding/results/v0.1.23.3/task2/e2e; mkdir -p $R/attach_r01/daemon_logs
NODED_ARGS="--arena-gib 48" bash codex_coding/src/v0.1.23.3/task2/live_graph/pool/pool_ctl.sh start $R/attach_r01/daemon_logs
M=$PWD/codex_coding/results/v0.1.23.3/task2/live_graph/pool_r02/manifests
```

1. Correctness and breakdown, Qwen3-0.6B on node28 card 6:

```sh
ssh node28 "docker exec zjn-fluid-pool-28 bash -lc 'cd $PWD && ulimit -c 0 && python3 codex_coding/src/v0.1.23.3/task2/e2e/attach_e2e.py $M $R/attach_r01/qwen3-0.6b --model qwen3-0.6b --node node28 --card 6 --reps 3'"
```

2. The same for 9g-8B (single card):

```sh
ssh node28 "docker exec zjn-fluid-pool-28 bash -lc 'cd $PWD && ulimit -c 0 && python3 codex_coding/src/v0.1.23.3/task2/e2e/attach_e2e.py $M $R/attach_r01/9g-8b --model 9g-8b --node node28 --card 6 --reps 3'"
```

   Acceptance for both:
   - `match: true` on every rep;
   - `dummy_engine.differs_from_reference: true`;
   - `attach_s` ≪ `restore_s` (expected tens of ms for 9g-8b).

3. Open-loop replay, three systems with the same arrival sequence:

```sh
for s in fluidpool local_dram disk; do
  ssh node28 "docker exec zjn-fluid-pool-28 bash -lc 'cd $PWD && ulimit -c 0 && python3 codex_coding/src/v0.1.23.3/task2/e2e/replay_ttft.py $M $R/replay_r01 --node node28 --cards 5,6 --models qwen3-0.6b,9g-8b --resident 1 --system $s --requests 200 --speedup 600'"
done
```

## 6. What remains to be verified on hardware

- IPC import of noded job buffers inside an SGLang TP worker on DTK.
- Greedy-output equality against a disk-loaded engine.
- `attach_s` magnitude.
- The HTTP attach route with ForkingPickler payloads.
- TP2.
- The replay skeleton end-to-end. It has not been executed yet, so expect small fixes.
