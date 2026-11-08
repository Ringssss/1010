# 首条import139：实际Torch重建入口的初始化契约

微probe把139定位到第一条storage0的`_new_shared_cuda`，此前`torch.cuda.is_initialized()`为false、native HIP current device为1。因首条就崩，本次首因不能归到“hash循环逐storage释放/重新open”；那条变量保留为后续完整验证的生命周期检查，不继续混入首错定位。

实际vendor安装代码 `/usr/local/lib/python3.10/dist-packages/torch/multiprocessing/reductions.py` 的`rebuild_cuda_tensor`在cache miss时，明确按以下顺序调用：

```python
torch.cuda._lazy_init()
storage = storage_cls._new_shared_cuda(...)
```

源码片段已只读保存于`vendor_torch_rebuild_cuda_tensor.txt`。`hash_target`直接调用低层`UntypedStorage._new_shared_cuda`，之前没有初始化Torch CUDA运行时；后面的`torch.empty(...device=...)`无法替第一次import先完成初始化。成熟`HbmTarget`则在导入前`torch.cuda.set_device(gpu)`，先触发Torch lazy initialization。

这提供明确的入口契约差异，和probe的“未初始化、首import即139”一致。set_device对照由父agent运行，须用其结果确认修订效果；本agent不凭退出码新增根因，不执行GPU、不修改负责agent实现。current device是否构成独立因素与初始化要求分开表述，不由set_device联合改变这两者的单次对照直接推出独立device因果。
