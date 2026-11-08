import os, sys, json, traceback
sys.path.insert(0, "/home/zhujianian/zhujianian/fluidckpt_zjn/main_lib")
import torch
from sglang.srt.model_loader import loader as L
from sglang.srt.model_loader.loader import _initialize_model, _get_quantization_config
from sglang.srt.configs.model_config import ModelConfig
from sglang.srt.server_args import ServerArgs

orig = L.DummyModelLoader.load_model

def patched(self, *, model_config, device_config):
    quant_config = _get_quantization_config(model_config, self.load_config)
    print("=== quant_config:", type(quant_config).__name__, getattr(quant_config, "get_name", lambda: None)())
    with torch.device(device_config.device):
        model = _initialize_model(model_config, self.load_config, quant_config)
    n = 0
    for name, mod in model.named_modules():
        if name.endswith("self_attn.kv_b_proj") and hasattr(mod, "weight"):
            w = mod.weight
            print(f"[{name}] shape={tuple(w.shape)} stride={w.stride()} dtype={w.dtype} "
                  f"scale_inv={getattr(mod,'weight_scale_inv',None) is not None} "
                  f"scale={getattr(mod,'weight_scale',None) is not None}")
            n += 1
            if n >= 3:
                break
    for pname, p in list(model.named_parameters())[:0]:
        pass
    for pname, p in model.named_parameters():
        if "layers.0.self_attn." in pname and ("kv" in pname or "q_" in pname or "w_" in pname):
            print(f"  param {pname} shape={tuple(p.shape)} dtype={p.dtype}")
    sys.stdout.flush()
    os._exit(0)

L.DummyModelLoader.load_model = patched
from sglang.srt.entrypoints.http_server import launch_server
from sglang.srt.server_args import prepare_server_args
args = prepare_server_args(sys.argv[1:])
launch_server(args)
