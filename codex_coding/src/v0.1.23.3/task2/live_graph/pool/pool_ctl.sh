#!/usr/bin/env bash
# Start / stop / inspect the node daemons (one per node, inside zjn-fluid-pool-<nn>).
#   pool_ctl.sh start <logdir> ["node=cards" ...]     default cards below
#   pool_ctl.sh stop | status
set -u
REPO=/home/zhujianian/zhujianian/fluidckpt_zjn
declare -A IP=([node27]=12.11.3.27 [node28]=12.11.3.28 [node29]=12.11.3.29 [node30]=12.11.3.30)
declare -A CARDS=([node27]=0,1,2,3,4,5,6,7 [node28]=0,1,2,3,4,5,6,7 [node29]=0,1,2,3,4,5,6,7 [node30]=0,1,2,3,4,5,6,7)
PAT='fluidckpt[.]live[.]noded'
cmd=${1:-status}
case "$cmd" in
  start)
    log=${2:?logdir}; shift 2; mkdir -p "$log"
    for kv in "$@"; do CARDS[${kv%%=*}]=${kv#*=}; done
    for n in node27 node28 node29 node30; do
      timeout 20 ssh -n -o BatchMode=yes "$n" "docker exec zjn-fluid-pool-${n#node} pkill -f '$PAT'; docker exec zjn-fluid-pool-${n#node} pkill -f 'pool/tenantd[.]py'; sleep 0.5; docker exec zjn-fluid-pool-${n#node} bash -lc 'cd $REPO; ulimit -c 0; (setsid nohup python3 -m fluidckpt.live.noded --node $n --ip ${IP[$n]} --cards ${CARDS[$n]} ${NODED_ARGS:-} > $log/noded_$n.log 2>&1 </dev/null &) ; (setsid nohup python3 codex_coding/src/v0.1.23.3/task2/live_graph/pool/tenantd.py ${IP[$n]} > $log/tenantd_$n.log 2>&1 </dev/null &) ; sleep 0.2'" &
    done; wait; echo started ;;
  stop)
    for n in node27 node28 node29 node30; do
      timeout 20 ssh -n -o BatchMode=yes "$n" "docker exec zjn-fluid-pool-${n#node} pkill -f '$PAT'; docker exec zjn-fluid-pool-${n#node} pkill -f 'pool/tenantd[.]py'; echo $n stopped" &
    done; wait ;;
  status)
    for n in node27 node28 node29 node30; do
      timeout 20 ssh -n -o BatchMode=yes "$n" "echo $n \$(docker exec zjn-fluid-pool-${n#node} pgrep -f '$PAT' | wc -l) procs" &
    done; wait ;;
esac
