"""Render independently collected physical endpoints without inferred links."""

import argparse
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]

def memtotal(text):
    return int(re.search(r"MemTotal:\s+(\d+) kB", text).group(1)) * 1024


def render(provenance, runtime):
    rows = []
    for node in ("node27", "node29"):
        host = json.loads((provenance / node / "host.json").read_text())
        dynamic = runtime["nodes"][node]["data"]
        smi = json.loads(dynamic["smi"]["memory_use"]["stdout"])
        inventory = json.loads((ROOT / f"codex_coding/results/v0.1.23.3/task2/discovery01/{node}_topology.json").read_text())["topology"]
        ordinals = {x["bdf"]: x["logical_gpu"] for x in inventory["gpus"]}
        gpus = sorted(host["kfd"], key=lambda x: int(x["properties"]["drm_render_minor"]))
        for numa in range(8):
            gpu = next(x for x in gpus if int(x["pci_chain"][0]["numa_node"]) == numa)
            chain = gpu["pci_chain"]
            root = chain[-1]["bdf"]
            ordinal = ordinals[chain[0]["bdf"]]
            hbm = int(smi[f"card{ordinal}"]["vram Total Memory (MiB)"]) * 2**20
            hcas = [x for x in host["hca"] if int(x["pci_chain"][0]["numa_node"]) == numa]
            hca_text = "; ".join(f"{x['name']} {x['pci_chain'][0]['bdf']} 400 Gb/s nominal" for x in hcas) or "HCA: none local"
            rows.append((node, numa, host["numa"][f"node{numa}"]["cpulist"],
                         memtotal(dynamic["numa_memory"][f"node{numa}"]),
                         gpu["properties"]["drm_render_minor"], chain[0]["bdf"], root, hca_text, ordinal, hbm))
    lines = ['<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="920" viewBox="0 0 1400 920">',
             '<rect width="1400" height="920" fill="#f6f8fb"/>',
             '<style>text{font-family:Arial,sans-serif;fill:#162333}.title{font-size:24px;font-weight:bold}.body{font-size:15px}.small{font-size:13px}</style>',
             '<text x="24" y="35" class="title">Physical resource map — node27 / node29</text>',
             '<text x="24" y="60" class="body">NUMA / CPU / DRAM and local PCI endpoints; no inferred GPU mesh or fabric capacity</text>']
    for i, (node, numa, cpus, dram, drm, bdf, root, hca_text, ordinal, hbm) in enumerate(rows):
        column = i // 8
        row = i % 8
        x, y = 24 + column * 692, 110 + row * 86
        if row == 0:
            lines.append(f'<text x="{x}" y="95" class="title">{node}</text>')
        lines.append(f'<rect x="{x}" y="{y}" width="665" height="78" rx="8" fill="white" stroke="#c4d1df"/>')
        texts = [f"NUMA {numa} | CPUs {cpus} | DRAM {dram / 2**30:.3f} GiB total",
                 f"GPU{ordinal} / DRM{drm} | {bdf} | HBM {hbm / 2**30:.3f} GiB total", hca_text]
        for j, text in enumerate(texts):
            lines.append(f'<text x="{x + 12}" y="{y + 21 + j * 23}" class="body">{html.escape(text)}</text>')
    lines.extend(['<rect x="24" y="810" width="1357" height="83" rx="8" fill="#fff4dc" stroke="#d2a44e"/>',
                  '<text x="38" y="835" class="body">Network fabric: topology and shared aggregate capacity UNKNOWN; each port nominal 400 Gb/s is not measured throughput.</text>',
                  '<text x="38" y="859" class="body">GPU interconnect: KFD link metadata retained separately; no measured complete pairwise bandwidth matrix.</text>',
                  '<text x="38" y="883" class="body">GPU ordinal: frozen discovery01 BDF mapping; HBM: fresh SMI reported total. Free capacity is not an authorized task budget.</text>', '</svg>'])
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    svg = render(args.provenance, json.loads(args.runtime.read_text()))
    (args.out / "physical_map.svg").write_text(svg)
    (args.out / "README.md").write_text("# 独立物理地图\n\n从两机原始 sysfs、NUMA meminfo和SMI独立生成，不读取资源地图实现。GPU ordinal引用冻结discovery01的BDF映射，同时显示DRM minor；不把当前未重新启动HIP验证的历史ordinal证据伪称fresh。每个NUMA的CPU集合、DRAM总量、GPU端点与HBM报告总量、局部HCA列出；400Gb/s仅为标称端口速率。网络fabric总容量和全GPU实测带宽矩阵保持未知；图中没有虚构边。\n")


if __name__ == "__main__":
    main()
