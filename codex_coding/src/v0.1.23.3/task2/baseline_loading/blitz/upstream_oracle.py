"""Compile CPU-only oracles extracted from the pinned upstream source, not the port."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[6]
SHA = "594e91127714349c183d935ac68d7b0eb63fc1ed"


def build(out, rustc):
    upstream = ROOT / "lib_cite/blitz-scale-hip"
    actual = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
    if actual != SHA:
        raise ValueError("upstream commit differs from frozen SHA")
    cache_path = upstream / "router_v2/src/replica/steersman/mod.rs"
    exec_path = upstream / "router_v2/src/replica/cybernetics/exec_blitz.rs"
    tp_path = upstream / "src/service/service.cc"
    cache = cache_path.read_text()
    # Remove only third-party serialization/logging dependencies; the algorithm
    # and Instant/AtomicUsize implementations remain the actual upstream code.
    cache = cache.replace("use serde::{Deserialize, Serialize};", "")
    cache = cache.replace("#[derive(Debug, Deserialize, Serialize)]", "#[derive(Debug)]")
    cache = re.sub(r"tracing::info!\(.*?\);", "", cache, flags=re.S)
    execution = exec_path.read_text()
    begin = execution.index("let rdma_sending_replicas = if cfg!")
    end = execution.index(";\n        rdma_sending_replicas", begin)
    selection = execution[begin:end+1]
    harness = r'''
#[derive(PartialEq)] enum ReplicaState { Inactive, RdmaSending }
fn main() {
    let mut s = Steersman::new(100000);
    s.register_model("qwen".into(), "processed".into(), 1.0);
    s.register_replica(0, 27); s.register_replica(2, 27); s.register_replica(4, 27);
    s.record_model_loaded(0);
    println!("local={:?}", s.assign_nvlink_chains("qwen", &vec![2,4]));
    s.wait_replica_nvlink(0, 1);
    println!("busy={:?}", s.assign_nvlink_chains("qwen", &vec![2]));
    s.post_replica_nvlink(0);
    s.record_model_unloaded(0);
    println!("ttl_empty={}", s.machine_has_cache(2, "qwen"));
    println!("ttl_no_sender={:?}", s.assign_nvlink_chains("qwen", &vec![2]));
    s.time_to_live_in_ms = 0;
    println!("expired={}", s.machine_has_cache(2, "qwen"));
    s.record_model_loaded(0);
    println!("loaded_beyond_ttl={}", s.machine_has_cache(2, "qwen"));
    let normal_prefill_replica_indices = VecDeque::from(vec![(1u32, 0usize)]);
    let normal_decode_replica_indices = VecDeque::from(vec![(2u32, 2usize), (3u32, 4usize)]);
    let shutting_decode_replica_indices = VecDeque::from(vec![(1u32, 6usize)]);
    let shutted_null_replica_indices = vec![8usize];
    let mut rdma_mrk_lck = vec![ReplicaState::Inactive; 9];
    rdma_mrk_lck[4] = ReplicaState::RdmaSending;
    SELECTOR
    println!("sender_order={:?}", rdma_sending_replicas);
    let mut src_replica_indices = rdma_sending_replicas;
    let src_replica_index = src_replica_indices.pop().unwrap();
    println!("selected_source={}", src_replica_index);
}
'''.replace("SELECTOR", selection).replace("vec![ReplicaState::Inactive; 9]", "(0..9).map(|_| ReplicaState::Inactive).collect::<Vec<_>>()")
    out.mkdir(parents=True, exist_ok=True)
    rust_source = out / "steersman_oracle.rs"
    rust_source.write_text(cache + "\nuse std::iter::FromIterator;\n" + harness)
    tool_usr = rustc.parent.parent
    rust_env = dict(os.environ, LD_LIBRARY_PATH=str(tool_usr / "lib/x86_64-linux-gnu"))
    compile_rust = subprocess.run([str(rustc), "--sysroot", str(tool_usr), "--edition=2021", str(rust_source),
                                   "-o", str(out / "steersman_oracle")], capture_output=True, text=True, env=rust_env)
    (out / "rust_build.log").write_text(compile_rust.stdout+compile_rust.stderr)
    compile_rust.check_returncode()
    rust_output = subprocess.check_output([str(out / "steersman_oracle")], text=True)
    tp = tp_path.read_text()
    begin = tp.index("std::vector<rank_t> ranks_in_chain {request->src_ranks(tp_rank)};")
    end = tp.index("        Assertion((ranks_in_chain.size() >= 2)", begin)
    tp_loop = tp[begin:end]
    cpp = r'''
#include <iostream>
#include <vector>
struct Request {
    std::vector<int> src, dst;
    int src_ranks(int rank) { return src.at(rank); }
    const std::vector<int>& dst_ranks() { return dst; }
};
int main() {
  using rank_t = int;
  for (int destinations : {1,2}) {
    Request storage{{0,1}, destinations == 1 ? std::vector<int>{2,3} : std::vector<int>{4,5,2,3}};
    Request* request = &storage;
    const int tp_size = 2;
    for (int tp_rank = 0; tp_rank < tp_size; ++tp_rank) {
      TP_LOOP
      std::cout << "tp" << destinations << ":" << tp_rank << "=";
      for (int rank : ranks_in_chain) std::cout << rank << ",";
      std::cout << "\n";
    }
  }
}
'''.replace("TP_LOOP", tp_loop)
    cpp_source = out / "tp_chain_oracle.cpp"
    cpp_source.write_text(cpp)
    compile_cpp = subprocess.run(["g++", "-std=c++17", str(cpp_source), "-o", str(out / "tp_chain_oracle")], capture_output=True, text=True)
    (out / "cpp_build.log").write_text(compile_cpp.stdout+compile_cpp.stderr)
    compile_cpp.check_returncode()
    cpp_output = subprocess.check_output([str(out / "tp_chain_oracle")], text=True)
    report = {"commit": SHA, "source_sha256": {str(p.relative_to(upstream)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (cache_path, exec_path, tp_path)}, "rust_stdout": rust_output, "cpp_stdout": cpp_output,
              "extraction_changes": ["remove serde and tracing only", "CPU fixture replaces controller state/RPC inputs",
                                     "TP loop compiled unchanged with a minimal request accessor shim"]}
    (out / "oracle.json").write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rustc", type=Path, required=True)
    args = parser.parse_args()
    build(args.out.resolve(), args.rustc.resolve())
