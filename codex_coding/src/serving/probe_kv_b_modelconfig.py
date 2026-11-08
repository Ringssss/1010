import sys, torch
sys.path.insert(0, "/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib")
from sglang.srt.server_args import ServerArgs, prepare_server_args
from sglang.srt.configs.model_config import ModelConfig
from sglang.srt.configs.device_config import DeviceConfig
from sglang.srt.configs.load_config import LoadConfig
from sglang.srt.model_loader.loader import _initialize_model, _get_quantization_config

def main():
    sa = prepare_server_args(sys.argv[1:])
    mc = ModelConfig.from_server_args(sa)
    lc = LoadConfig(load_format="dummy")
    qc = _get_quantization_config(mc, lc)
    print("QUANT:", type(qc).__name__, qc)
    import sglang.srt.model_executor.model_runner as _mr
    print("_is_cuda:", getattr(_mr, "_is_cuda", "?"), "_is_hip:", getattr(_mr, "_is_hip", "?"))
    with torch.device("meta"):
        model = _initialize_model(mc, lc, qc)
    print("=== attn modules (layer 0/1) ===")
    shown = 0
    for name, mod in model.named_modules():
        if ".self_attn" in name and not any(x in name for x in (".indexer", "rotary", "norm")):
            print(f"{name}: {type(mod).__name__}")
        if name.count(".") and name.endswith("self_attn") and shown < 2:
            shown += 1
            for attr in ("kv_b_proj",):
                m = getattr(mod, attr, None)
                if m is not None and hasattr(m, "weight"):
                    w = m.weight
                    print(f"  [{name}.{attr}] shape={tuple(w.shape)} stride={w.stride()} dtype={w.dtype}")
                for extra in ("weight_scale_inv", "weight_scale"):
                    t = getattr(m, extra, None)
                    if t is not None:
                        print(f"     {extra}: shape={tuple(t.shape)} dtype={t.dtype}")
            for attr in ("w_kc", "w_vc", "w_scale"):
                print(f"  [{name}.{attr}] = {getattr(mod, attr, 'MISSING')}")
    print("=== layer0.self_attn named_parameters ===")
    for n, p in model.named_parameters():
        if n.startswith("model.layers.0.self_attn."):
            print(f"  {n}: {tuple(p.shape)} {p.dtype}")

if __name__ == "__main__":
    main()
