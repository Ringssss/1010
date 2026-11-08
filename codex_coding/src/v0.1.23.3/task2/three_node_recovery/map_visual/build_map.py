#!/usr/bin/env python3
"""Build an evidence-bound resource map without contacting remote hosts."""

import argparse
import ctypes
import ctypes.util
import hashlib
import json
import re
from html import escape
from pathlib import Path


ROOT = Path(__file__).resolve().parents[6]
RESULTS = Path('codex_coding/results/v0.1.23.3/task2')
RECOVERY = RESULTS / 'three_node_recovery'
GREEN = '#16815c'
GRAY = '#98a4b3'
INK = '#213047'


def read_json(path):
    return json.loads((ROOT / path).read_text())


def sha(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def render_png(svg_path, png_path):
    # Use system libraries so the generator needs no Python packages.
    rsvg = ctypes.CDLL(ctypes.util.find_library('rsvg-2'))
    cairo = ctypes.CDLL(ctypes.util.find_library('cairo'))
    gobject = ctypes.CDLL(ctypes.util.find_library('gobject-2.0'))
    rsvg.rsvg_handle_new_from_file.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
    rsvg.rsvg_handle_new_from_file.restype = ctypes.c_void_p
    rsvg.rsvg_handle_render_cairo.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    rsvg.rsvg_handle_render_cairo.restype = ctypes.c_int
    cairo.cairo_image_surface_create.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
    cairo.cairo_image_surface_create.restype = ctypes.c_void_p
    cairo.cairo_create.argtypes = [ctypes.c_void_p]
    cairo.cairo_create.restype = ctypes.c_void_p
    cairo.cairo_surface_write_to_png.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    cairo.cairo_surface_write_to_png.restype = ctypes.c_int
    cairo.cairo_destroy.argtypes = [ctypes.c_void_p]
    cairo.cairo_surface_destroy.argtypes = [ctypes.c_void_p]
    gobject.g_object_unref.argtypes = [ctypes.c_void_p]
    handle = rsvg.rsvg_handle_new_from_file(str(svg_path).encode(), None)
    if not handle:
        raise RuntimeError('librsvg could not open the SVG')
    surface = cairo.cairo_image_surface_create(0, 1600, 1060)
    context = cairo.cairo_create(surface)
    try:
        if not rsvg.rsvg_handle_render_cairo(handle, context):
            raise RuntimeError('librsvg rendering failed')
        if cairo.cairo_surface_write_to_png(surface, str(png_path).encode()):
            raise RuntimeError('PNG write failed')
    finally:
        cairo.cairo_destroy(context)
        cairo.cairo_surface_destroy(surface)
        gobject.g_object_unref(handle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=RECOVERY / 'map_visual_r01')
    args = parser.parse_args()
    out = ROOT / args.output
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise SystemExit('Output directory is not empty; choose a fresh run name.')
    qualification_path = RECOVERY / 'review_runtime_final_r01/qualification.json'
    refs_path = RECOVERY / 'review_runtime_final_r01/evidence_refs.json'
    qualification = read_json(qualification_path)
    assert qualification['actual_IB_path_passed']
    assert not qualification['performance_ready']
    assert qualification['payload']['bytes'] == 1048576
    refs = read_json(refs_path)
    for path, metadata in refs.items():
        assert sha(Path(path)) == metadata['sha256'], path
    refs = {**refs, str(qualification_path): {'sha256': sha(qualification_path)},
            str(refs_path): {'sha256': sha(refs_path)}}
    summary = {'development': 'v0.1.23.3/task2', 'qualified_at_UTC': qualification['qualified_at_UTC'],
               'qualified_scope': qualification['qualified_scope'], 'live_query_performed': False,
               'staging_page_NUMA_measured': False, 'performance_ready': False, 'nodes': {}}
    for node in ['node28', 'node30']:
        path = RECOVERY / 'inventory_r03' / f'{node}.stdout.json'
        refs[str(path)] = {'sha256': sha(path)}
        raw = read_json(path)
        gpus = []
        for device in raw['drm']:
            endpoint = device['pci_chain'][0]
            if endpoint['class'] != '0x0b4000' or endpoint['vendor'] != '0x1d94':
                continue
            ordinal = int(device['render_nodes'][0].removeprefix('renderD')) - 128
            gpus.append({'physical_gpu': ordinal, 'BDF': endpoint['bdf'],
                         'NUMA': int(endpoint['numa_node']), 'local_cpulist': endpoint['local_cpulist'],
                         'sysfs_vram_total_bytes': int(device['mem_info_vram_total'])})
        gpus.sort(key=lambda item: item['physical_gpu'])
        hcas = [{'name': item['name'], 'BDF': item['pci_chain'][0]['bdf'],
                 'NUMA': int(item['pci_chain'][0]['numa_node']),
                 'local_cpulist': item['pci_chain'][0]['local_cpulist'], 'ports': item['ports']}
                for item in raw['hca']]
        selected = qualification['nodes'][node]
        assert gpus[selected['physical_gpu']]['BDF'] == selected['BDF']
        assert len(gpus) == 8 and len(hcas) == 4 and len(raw['numa']) == 8
        assert selected['actual_transport'] == 'NET/IBext_v8' and selected['gdr_policy'] == 0
        memory_kib = int(re.search(r'MemTotal:\s+(\d+)', raw['memory']).group(1))
        summary['nodes'][node] = {'snapshot_UTC': raw['started_utc'], 'hostname': raw['hostname'],
                                 'logical_CPUs': raw['cpu_count'], 'NUMA_count': len(raw['numa']),
                                 'MemTotal_bytes': memory_kib * 1024, 'MemTotal_GiB': memory_kib / 1048576,
                                 'GPU': gpus, 'HCA': hcas, 'tested_physical_gpu': selected['physical_gpu'],
                                 'tested_HCA': selected['selected_HCA'], 'gdr_policy': selected['gdr_policy']}
    refs['docx/next_step.txt'] = {'sha256': sha(Path('docx/next_step.txt')), 'role': 'node27 historical role context'}
    refs[str(RECOVERY / 'inventory_qualification_r02/qualification.json')] = {
        'sha256': sha(RECOVERY / 'inventory_qualification_r02/qualification.json'), 'role': 'node27 pending snapshot'}
    historical_path = RESULTS / 'topology_alignment/provenance_r01/node27/host.json'
    historical = read_json(historical_path)
    assert len(historical['kfd']) == 8 and len(historical['numa']) == 8 and len(historical['hca']) == 4
    refs[str(historical_path)] = {'sha256': sha(historical_path), 'role': 'node27 historical physical structure'}
    for path in ['resource_map.svg', 'resource_map.png', 'resource_summary.json', 'evidence_refs.json', 'report.md']:
        assert not (out / path).exists()
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="1060" viewBox="0 0 1600 1060">',
             '<defs><marker id="arrow" markerUnits="userSpaceOnUse" markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto-start-reverse"><path d="M0 0L8 4L0 8Z" fill="#16815c"/></marker></defs>',
             '<rect width="1600" height="1060" fill="#f5f7fa"/>',
             '<g font-family="Droid Sans Fallback, DejaVu Sans, sans-serif">']

    def box(x, y, w, h, fill='#ffffff', stroke='#d9e0e9', width=1.5, radius=15):
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>')

    def text(x, y, label, size=20, color=INK, bold=False):
        parts.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{700 if bold else 400}">{escape(str(label))}</text>')

    def line(x1, y1, x2, y2, dashed=False):
        parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{GREEN}" stroke-width="3" marker-start="url(#arrow)" marker-end="url(#arrow)"'+(' stroke-dasharray="6 5"' if dashed else '')+'/>')

    text(48, 61, '当前资源地图：28 ↔ 30 一条 IB 路径功能通过', 33, bold=True)
    text(48, 102, 'v0.1.23.3 / task2 · 本地图依据 2026-10-08 物理快照与 2026-10-09 15:25 北京时间功能验收', 19, '#5b6b80')
    for x, color, label in [(48, GREEN, '绿色：选定路径已实测'), (438, '#c18b16', '黄色：历史角色 / 新运行待确认'), (992, GRAY, '灰色：结构已采集 / 路径未测')]:
        box(x, 123, 16, 16, color, color, radius=4)
        text(x + 27, 138, label, 18, '#5b6b80')
    box(48, 171, 320, 363, '#fff9e9', '#d5a12c', 2)
    text(70, 212, 'node27', 27, '#906717', True)
    text(70, 248, '历史控制 / DRAM 底本节点', 20, '#906717')
    for y, label in [(282, '历史结构：8GPU / 8NUMA / 4HCA'), (315, '已有 keeper 与目录历史证据'), (348, '新 GPU 运行尚未复核'), (381, 'D 进程退出未重新确认'), (421, 'pending: PID 4037472'), (451, 'start_ticks: 477235183'), (501, '本轮 28/30 验收未访问 node27')]:
        text(70, y, label, 18, '#705f38')
    box(48, 552, 320, 98, '#eef1f5', '#b7c1ce')
    text(70, 586, 'node29 · 本轮排除', 21, '#6d798a', True)
    text(70, 614, '旧 pending: PID 21883 / 21901', 17, '#6d798a')
    text(70, 640, '控制恢复与退出状态仍待证', 17, '#6d798a')
    for node, x in [('node28', 396), ('node30', 988)]:
        n = summary['nodes'][node]
        box(x, 171, 564, 479)
        text(x+22, 211, node, 27, bold=True)
        box(x+359, 189, 183, 32, '#e9f5ef', '#b1d9c7', radius=8)
        text(x+371, 212, '选定端点功能通过', 18, GREEN, True)
        text(x+22, 247, f"8 GPU / 8 NUMA / 4 HCA · {n['logical_CPUs']} 逻辑 CPU", 21)
        text(x+22, 279, f"主存总量 {n['MemTotal_GiB']:.2f} GiB · 10/08 静态快照", 19, '#5b6b80')
        text(x+22, 315, 'GPU：约 64 GiB/卡 · PCI 物理 NUMA 归属', 18, '#5b6b80')
        for i, gpu in enumerate(n['GPU']):
            xx, yy = x+22+(i%4)*133, 332+(i//4)*78
            selected = i == n['tested_physical_gpu']
            box(xx, yy, 121, 66, '#e9f5ef' if selected else '#f2f4f7', GREEN if selected else '#ced6e0', 2 if selected else 1, 8)
            text(xx+10, yy+26, f'GPU{i} · {gpu["BDF"][5:7]}', 18, GREEN if selected else '#6d798a', True)
            text(xx+10, yy+51, f'NUMA {gpu["NUMA"]}', 17, GREEN if selected else '#6d798a')
        text(x+22, 505, 'HCA：InfiniBand 网卡 / 端口 1', 18, '#5b6b80')
        for i, hca in enumerate(n['HCA']):
            xx, yy = x+22+i*133, 523
            box(xx, yy, 121, 66, '#e9f5ef' if i==0 else '#f2f4f7', GREEN if i==0 else '#ced6e0', 2 if i==0 else 1, 8)
            text(xx+10, yy+26, hca['name'], 18, GREEN if i==0 else '#6d798a', True)
            text(xx+10, yy+51, f'{hca["BDF"][5:7]} · NUMA{hca["NUMA"]}', 16, GREEN if i==0 else '#6d798a')
        text(x+22, 624, 'PCI 归属用于拓扑定位；DRAM 页归属需单独测定', 17, '#5b6b80')
    box(48, 674, 1504, 227, '#eef8f3', '#abd3bf')
    text(70, 712, '已验证的端到端路径', 25, GREEN, True)
    text(379, 710, '两 rank · 实际 NET/IBext_v8 · shca_0:1 · gdr0 · 自有 XML runtime 接受', 20, GREEN)
    flow = [(70, 140, ['node28 GPU2', '55 · NUMA2']),
            (251, 190, ['CPU staging', '实际页 NUMA 未测']),
            (482, 150, ['shca_0:1', '03 · NUMA0']),
            (673, 160, ['IB 交换网络', '内部路由未取证']),
            (874, 150, ['shca_0:1', '03 · NUMA0']),
            (1065, 190, ['CPU staging', '实际页 NUMA 未测']),
            (1296, 234, ['node30 GPU0', '09 · NUMA0'])]
    for i, (x, width, labels) in enumerate(flow):
        is_stage = i in [1, 5]
        box(x, 739, width, 72, '#ffffff', '#8abda4', radius=35 if i==3 else 9)
        text(x+12, 767, labels[0], 19, GREEN, True)
        text(x+12, 795, labels[1], 16, '#5b6b80')
        if i < len(flow)-1:
            line(x+width+8, 775, flow[i+1][0]-8, 775, dashed=i in [0,1,4,5])
    text(70, 849, 'staging 与虚线为 gdr0 机制示意；没有测定实际 DRAM 数据页布局或每一段复制细节。', 19, '#5b6b80')
    text(70, 881, '实测：全 1 MiB 内容 SHA 一致、自然 rc0、无 signal、进程组空；仅上述 GPU / HCA 组合通过。', 19, GREEN)
    text(48, 944, '资源边界', 22, bold=True)
    text(183, 944, 'GPU 负载共享；未测带宽、竞争或权重可用时延。node30 根盘 10/09 取证可用 0 B，小资产使用私有 shm。', 18, '#5b6b80')
    text(48, 982, '时间边界', 22, bold=True)
    text(183, 982, '总容量来自 10/08 静态 snapshot；free / 可调度额度是动态量，此图不提供实时可用数。', 18, '#5b6b80')
    text(48, 1020, '读图提示', 22, bold=True)
    text(183, 1020, 'GPU 后缀为 PCI 总线号；node 是服务器，NUMA 是机内内存域；400 Gb/s ≠ 实测吞吐。完整表见同目录报告。', 18, '#5b6b80')
    parts.append('</g></svg>')
    (out / 'resource_map.svg').write_text('\n'.join(parts))
    render_png(out / 'resource_map.svg', out / 'resource_map.png')
    (out / 'resource_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    (out / 'evidence_refs.json').write_text(json.dumps(refs, ensure_ascii=False, indent=2)+'\n')
    rows = ['# 资源地图证据说明', '', '图中绿色仅表示28/30已验收的两rank路径功能通过；黄色node27为历史控制/DRAM底本角色且新GPU运行与D进程退出未重新确认；灰色为结构信息或本轮排除的node29。未进行远程查询或实验。', '',
            '物理快照为2026-10-08 08:00:58 UTC（16:00:58北京时间）；功能资格时间2026-10-09 07:25:33 UTC（15:25:33北京时间）。两机各128逻辑CPU、8NUMA、8GPU、4HCA、主存528095244KiB=503.630870819GiB。CPU插槽数未推断。', '',
            'GPU静态序号按renderD128–135排列，只有最终资格中的28 GPU2/BDF55及30 GPU0/BDF09由fresh HIP BDF绑定；其他GPU没有逐卡runtime验收。两机静态映射一致，BDF是物理身份。PCI NUMA归属是设备拓扑信息，不能表示数据页实际placement。每卡DRM total68702699520B约63.984GiB（10/08快照），不等于HIP free或可用预算。NUMA0–7是机内内存域，node27/28/30是服务器。', '',
            '| 物理GPU | 完整BDF（两机一致） | NUMA | local_cpulist |', '|---|---|---:|---|']
    for g in summary['nodes']['node28']['GPU']:
        rows.append(f'| GPU{g["physical_gpu"]} | {g["BDF"]} | {g["NUMA"]} | {g["local_cpulist"]} |')
    rows.extend(['', '| HCA（两机一致） | 完整BDF | NUMA | local_cpulist | 10/08端口1快照 |', '|---|---|---:|---|---|'])
    for h in summary['nodes']['node28']['HCA']:
        rows.append(f'| {h["name"]} | {h["BDF"]} | {h["NUMA"]} | {h["local_cpulist"]} | ACTIVE / LinkUp / 400 Gb/s |')
    rows.extend(['', '实测仅node28 GPU2/0000:55:00.0/NUMA2 → shca_0:1/0000:03:00.0/NUMA0 ↔ node30 shca_0:1/0000:03:00.0/NUMA0 → GPU0/0000:09:00.0/NUMA0，实际IBext_v8、gdr0。两端全1048576B SHA=631b84027d6b9e52b539c4e8373622d23032dfadc64d60af87339c9037e4f769，19项验收通过、自然退出、无signal、组空。staging仅按gdr0机制示意，未测实际页NUMA与分段复制。', '',
                 '边界：GPU非独占；带宽、共享竞争与weights-ready未测；其他设备组合没有runtime资格。IB交换网络内部路由、共享上行与容量未取证，图中网络框不代表两NIC线缆直连。node30根盘在10/09资格记录中available=0B，小资产在私有shm。node27 PID4037472/start477235183及node29 PID21883/start475875814、21901/start475875846没有新退出证明。原XML/default mapping保持。node27历史结构来自topology_alignment/provenance_r01/node27/host.json，仅历史物理事实，不合计成三节点可用预算。', '',
                 '证据：evidence_refs.json包含两份inventory原件、最终资格、独立验收索引及其45份取证原件SHA；生成时逐项验证最终索引原件SHA。resource_summary.json保存精确映射和时间。', '',
                 '本地图为派生说明，不覆盖旧resource_map、solver、正式文档或历史记录。', '',
                 '复现：在仓库根目录执行独立脚本，选择新结果目录；使用Python标准库及系统librsvg/cairo，不需要GPU。', '',
                 '```bash', 'python3 codex_coding/src/v0.1.23.3/task2/three_node_recovery/map_visual/build_map.py --output codex_coding/results/v0.1.23.3/task2/three_node_recovery/map_visual_r02', '```', ''])
    (out / 'report.md').write_text('\n'.join(rows))
    print(json.dumps({'output': str(out), 'source_refs': len(refs), 'size': [1600, 1060]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
