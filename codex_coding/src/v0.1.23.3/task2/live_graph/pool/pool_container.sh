#!/usr/bin/env bash
# Start (or restart) the FluidPool runtime container on one node.
#
# Unlike gpu/cluster_container.sh this exposes every card with HOST ordinals (no
# HIP_VISIBLE_DEVICES renumbering): the node daemon is told which cards it may touch and never
# creates a context on the others. The node's local NVMe pool directory is mounted at /ssdpool.
# Nothing is ever written to the container's own filesystem (node27's root disk is 100% full).
#
# Usage: pool_container.sh <node>
set -eu
node=${1:?node}
name=zjn-fluid-pool-${node#node}
home=/home/zhujianian
img=harbor.sourcefind.cn:5443/dcu/admin/base/sglang:0.5.12-ubuntu22.04-dtk2604-py3.10-sf_b075
if [ "$node" = node27 ]; then ssd=$home/zhujianian/fluidpool_ssd27; else ssd=/data/$node/shared/zjn_fluidpool; fi

ssh -n -o BatchMode=yes "$node" "
  mkdir -p $ssd
  docker rm -f $name >/dev/null 2>&1 || true
  docker run -d --name $name --network host --ipc private --shm-size 1g \
    --device /dev/kfd --device /dev/mkfd --device /dev/dri --device /dev/infiniband \
    --cap-add IPC_LOCK --cap-add SYS_NICE \
    --ulimit core=0 --ulimit memlock=-1:-1 --ulimit nofile=1048576:1048576 \
    -e HSA_FORCE_FINE_GRAIN_PCIE=1 \
    -e LD_LIBRARY_PATH=/opt/dtk/lib:/opt/dtk/lib64:/opt/hyhal/lib:/opt/hyhal/lib64:/opt/dtk/hip/lib:/opt/dtk/llvm/lib \
    -e TZ=Asia/Shanghai -e PYTHONPATH=$home/zhujianian/fluidckpt_zjn/main_lib -e PYTHONDONTWRITEBYTECODE=1 \
    -v $home:$home:rw -v /data:/data:ro -v $ssd:/ssdpool:rw -v /opt/hyhal:/opt/hyhal:ro \
    -w $home/zhujianian/fluidckpt_zjn \
    $img sleep infinity >/dev/null
  docker exec $name bash -lc 'python3 -c \"import torch;print(\\\"$node cards\\\",torch.cuda.device_count())\"; df -h /ssdpool | tail -1'
"
