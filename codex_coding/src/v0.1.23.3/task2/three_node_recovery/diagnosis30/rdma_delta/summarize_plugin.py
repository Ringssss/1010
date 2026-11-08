"""Record the Socket fallback and the existing successful vendor plugin configuration."""
import hashlib
import json
from pathlib import Path


def reference(path):
    return {"path":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    root=Path(__file__).resolve().parents[7]
    base=root/"codex_coding/results/v0.1.23.3/task2"
    recovery=base/"three_node_recovery"
    out=recovery/"diagnosis30_rdma_analysis_r01"
    out.mkdir(exist_ok=False)
    olddir=base/"topology_alignment/probe_startup_empty_r01"
    oldenv=olddir/"rank_0.environment.json"
    oldlog=olddir/"rank_0.nccl.12142.log"
    oldprovenance=base/"topology_alignment/provenance_r01/node27/container.json"
    env=json.loads(oldenv.read_text())["effective_environment"]
    plugin=next(row for row in json.loads(oldprovenance.read_text())["files"] if row["path"].endswith("librccl-net-shca.so"))
    oldlines=[line for line in oldlog.read_text().splitlines() if "Plugin name set by env" in line or "Plugin Path" in line or "NET/IB : Using" in line or "Using network" in line or ("Channel 00/0" in line and "NET/IBext" in line)]
    nodes=[]
    refs=[reference(path) for path in (oldenv,oldlog,oldprovenance)]
    for node in ("node28","node30"):
        metadata_path=recovery/("diagnosis30_rdma_metadata_"+node+"_r01")/"stdout.json"
        metadata=json.loads(metadata_path.read_text())
        info=metadata["container_metadata"]
        actual=next(row for row in info["files"] if row["path"].endswith("librccl-net-shca.so"))
        assert actual["exists"] and actual["sha256"]==plugin["sha256"]
        current_path=recovery/("probe_nccl_2830_"+node+"_r01")/"nccl.log"
        current_lines=[line for line in current_path.read_text().splitlines() if "NET/Plugin" in line or "No device found" in line or "Using network" in line or "NCCL_IB_HCA set" in line or 'Missing "HSA_FORCE' in line]
        nodes.append({"node":node,"plugin":actual,"same_plugin_sha_as_historical_success":True,"base_container_env":info["env_whitelist"],"other_library_metadata":info["files"],"current_owned_log_lines":current_lines,"metadata_read_only":True})
        refs.extend([reference(metadata_path),reference(current_path)])
    data={"historical_actual_transport":"NET/IBext_v8 over shca_0:1 (non-GDR empty_seed run)","historical_env":{key:env.get(key) for key in ("NCCL_NET_PLUGIN","NCCL_IB_HCA","HSA_FORCE_FINE_GRAIN_PCIE","LD_LIBRARY_PATH")},
          "historical_plugin":plugin,"historical_owned_log_lines":oldlines,"current_nodes":nodes,
          "current_r01_transport":"Socket; full 1MiB payload SHA success is not RDMA evidence",
          "observed_fallback_chain":"default librccl-net.so missing -> internal network plugin -> NET/IB no device with requested selector -> Socket",
          "supported_small_fix":"restore explicit librccl-net-shca.so and HSA_FORCE_FINE_GRAIN_PCIE=1; parent may request IBext_v8 explicitly to refuse Socket fallback",
          "selector_boundary":"historical shca_0 -> actual port1; current =shca_0:1 handling by vendor plugin not yet measured; no renamed alias established",
          "interpretation":"platform compatibility repair, not a single-variable performance experiment; actual next-rank plugin load and channels decide IB acceptance",
          "gdr_policy":"owned physical XML remains gdr0; IB success would not certify GDR",
          "raw_refs":refs,"new_gpu_operations":0,"library_loads":0,"verbs_queries":0,"remote_file_writes":0}
    (out/"summary.json").write_text(json.dumps(data,indent=2,ensure_ascii=False)+"\n")
    (out/"source.py").write_bytes(Path(__file__).read_bytes())
    (out/"report.md").write_text("""# node28/30 NCCL Socket回落：恢复现成平台插件配置\n\n当前r01两端日志准确链路为：未找到默认librccl-net.so，改内部network plugin；请求=shca_0:1后NET/IB无device，最终Using network Socket。全1MiB payload SHA通过只认证数据功能，不能升级RDMA。r01的dump解析失败另由A/C修复，不能与传输选择混为同一个原因。\n\n历史真成功原件topology_alignment/probe_startup_empty_r01的child12142明确记录env选librccl-net-shca.so、Plugin Path=/opt/rccl-rdma-sharp-plugins/lib/librccl-net-shca.so、NET/IB Using[0]shca_0:1/IB、Using network IBext_v8及channel NET/IBext_v8。对应ENV有NCCL_NET_PLUGIN=librccl-net-shca.so、NCCL_IB_HCA=shca_0、HSA_FORCE_FINE_GRAIN_PCIE=1。该empty_seed组本身non-GDR，已有别的旧GDR组也走同vendor插件，但不拿其GDR能力替代本次自有gdr0 policy。\n\n28/30各一次当前精确容器CPU只读取证：host/boot/fullCID/StartedAt/image/tasklabel和PID namespace前后通过，vendor插件均752,144B、mode0755、SHA e0c3fd25c91cdb19c078b8350dd9daadded89d66600b0e5ed298840658af3af6，与旧成功原件完全一致；libshca.so两端SHA781c787953e5c57d178407b50223d61f3521ae7bcae436eefdbe01a4c48f4515也同旧运行，libibverbs.so.1两端均a57430a6ab66c3fcbbfde8b5e4fed0232016207fe017a49e88b83b4091e631c9。基底LD_LIBRARY_PATH已有插件目录，默认basename路径librccl-net.so确实没有，当前基底env未显式选vendor插件/fine-grain；实际r01启动参数和日志也没有该显式配置。不能说vendor RDMA库未安装或core库搜索路径缺失。\n\n最小可审的兼容配置恢复为父指定NCCL_NET_PLUGIN=librccl-net-shca.so、HSA_FORCE_FINE_GRAIN_PCIE=1，并显式NCCL_NET=IBext_v8使其他network不能静默回落。这是恢复现成平台配置加严格传输要求，不是性能单变量归因。HCA名字shca_0已在历史真NET/IB日志实际出现，当前sysfs同名不能说alias错；vendor插件是否接受当前=shca_0:1 selector尚未本轮实测，父可直接复用旧shca_0表达并要求实际selected port回传1。\n\n下一次功能运行仍须每rank出现本PID/rank的vendor Plugin Path、Using IBext_v8、实际shca_0:1/IB、cross NET/IBext_v8 channel与nNodes>1、全1MiB SHA，以及准确owned input XML载入和dump selectedBDF/CPU/chain校验、cleanup和前后保护。若这些没出现，诚实保留Socket-only或启动失败，不冒认IB/GDR/带宽。自有XML及默认mapping保持，gdr0不改。\n\n本调查不导入Torch、不load插件/GPU库、不verbs查询或通信，也不执行新的GPU实验。现成插件存在且同SHA已够准备下一兼容组，停止扩大hardware/library调查。全部原件/SHA与准确ENV见summary.json，父/A协调r02命令，C独立审。\n""")
    print(json.dumps({"out":str(out),"both_plugins_match_historical_success":True,"new_gpu_operations":0}))


if __name__=="__main__":main()
