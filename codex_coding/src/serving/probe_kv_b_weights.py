import os, sys, torch
sys.path.insert(0, "/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib")
from sglang.srt.model_loader import loader as L

def dump(model, tag):
    print(f"=== {tag}: quant_config={type(getattr(model,'quant_config',None)).__name__}", flush=True)
    for name, mod in model.named_modules():
        if not name.endswith("self_attn") or name.count("layers.") != 1:
            continue
        kb = getattr(mod, "kv_b_proj", None)
        if kb is not None and hasattr(kb, "weight"):
            w = kb.weight
            print(f"  [{name}.kv_b_proj.weight] shape={tuple(w.shape)} stride={w.stride()} dtype={w.dtype}", flush=True)
            for extra in ("weight_scale_inv", "weight_scale"):
                t = getattr(kb, extra, None)
                if t is not None:
                    print(f"     {extra}: {tuple(t.shape)} {t.dtype}", flush=True)
            print(f"     attrs: w_kc={getattr(mod,'w_kc',None)!r}"[:120], flush=True)
        break
    ps = [(n, p) for n, p in model.named_parameters() if n.startswith("model.layers.0.self_attn.")]
    print(f"  layer0.self_attn params ({len(ps)}):", flush=True)
    for n, p in ps[:12]:
        print(f"    {n}: {tuple(p.shape)} {p.dtype}", flush=True)
    print(f"  total params: {sum(1 for _ in model.named_parameters())}", flush=True)

orig = L.DummyModelLoader.load_model
def patched(self, *, model_config, device_config):
    quant_config = L._get_quantization_config(model_config, self.load_config)
    with L.set_default_torch_dtype(model_config.dtype):
        with torch.device(device_config.device):
            model = L._initialize_model(model_config, self.load_config, quant_config)
        for _, module in model.named_modules():
            qm = getattr(module, "quant_method", None)
            if qm is not None:
                if hasattr(module, "is_weights_quantized") and module.is_weights_quantized():
                    continue
                qm.process_weights_after_loading(module)
        from sglang.srt.model_loader.weight_utils import initialize_dummy_weights
        initialize_dummy_weights(model)
        dump(model, "AFTER dummy init + process_weights, BEFORE _post_load_weights")
        try:
            L._post_load_weights(model)
            print("!!! _post_load_weights SUCCEEDED", flush=True)
            dump(model, "AFTER _post_load_weights")
        except Exception as e:
            print(f"!!! _post_load_weights FAILED: {type(e).__name__}: {e}", flush=True)
        import traceback; traceback.print_exc()
    sys.stdout.flush(); os._exit(0)

L.DummyModelLoader.load_model = patched

from sglang.srt.entrypoints.http_server import launch_server
if __name__ == "__main__":
    from sglang.srt.server_args import prepare_server_args
    launch_server(prepare_server_args(sys.argv[1:]))
