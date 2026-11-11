#!/usr/bin/env bash
# Start (or restart) the FluidCKPT live-graph runtime container on one node.
#
# The container mounts the LIVE repository (not a frozen copy), exposes all accelerator devices
# and IB, but restricts the visible GPUs with HIP_VISIBLE_DEVICES so only cards this project owns
# are touched. Inside the container ordinals are renumbered from 0.
#
# Usage: cluster_container.sh <node> <visible_host_ordinals>   e.g. cluster_container.sh node27 4,5,6,7
set -eu
node=${1:?node}
vis=${2:?visible host ordinals, e.g. 4,5,6,7}
name=${NAME:-zjn-fluid-live-r01-${node#node}}
repo=/home/zhujianian/zhujianian/fluidckpt_zjn
img=harbor.sourcefind.cn:5443/dcu/admin/base/sglang:0.5.12-ubuntu22.04-dtk2604-py3.10-sf_b075

ssh -n -o BatchMode=yes "$node" "
  docker rm -f $name >/dev/null 2>&1 || true
  docker run -d --name $name --network host --ipc private --shm-size 16g \
    --device /dev/kfd --device /dev/mkfd --device /dev/dri --device /dev/infiniband \
    --ulimit core=0 --ulimit memlock=-1:-1 --ulimit nofile=1048576:1048576 \
    -e HIP_VISIBLE_DEVICES=$vis \
    -e HSA_FORCE_FINE_GRAIN_PCIE=1 \
    -e NCCL_NET_PLUGIN=librccl-net-shca.so -e NCCL_SOCKET_IFNAME=ib0 \
    -e GLOO_SOCKET_IFNAME=ib0 -e NCCL_DEBUG=WARN \
    -e NCCL_TOPO_FILE=/usr/local/built-in-508-topo-input-tj-default.xml \
    -e LD_LIBRARY_PATH=/opt/rccl-rdma-sharp-plugins/lib:/opt/dtk/lib:/opt/dtk/lib64:/opt/hyhal/lib:/opt/hyhal/lib64:/opt/dtk/rccl/lib:/opt/dtk/hip/lib:/opt/dtk/llvm/lib \
    -e TZ=Asia/Shanghai \
    -v $repo:$repo:rw \
    -v /home/zhujianian:/home/zhujianian:rw \
    -v /data:/data:ro \
    -v /opt/hyhal:/opt/hyhal:ro \
    -w $repo \
    $img sleep infinity >/dev/null
  docker exec $name bash -lc 'python3 -c \"import torch;print(\\\"visible\\\",torch.cuda.device_count())\"'
"
