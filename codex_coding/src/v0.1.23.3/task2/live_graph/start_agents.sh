#!/usr/bin/env bash
# Start / stop the read-only node probe publisher on this host.
# Usage: start_agents.sh start <node> <port> <logdir> | start_agents.sh stop
set -u
ROOT=/home/zhujianian/zhujianian/fluidckpt_zjn
PATTERN='fluidckpt[.]live[.]agent'          # bracket keeps pkill from matching this script
cmd=${1:-start}

case "$cmd" in
  stop)
    pkill -f "$PATTERN" && echo stopped || echo "not running"
    ;;
  start)
    node=${2:?node}
    port=${3:-18740}
    logdir=${4:?logdir}
    mkdir -p "$logdir"
    pkill -f "$PATTERN" 2>/dev/null
    sleep 0.3
    cd "$ROOT" || exit 1
    PYTHONPATH=main_lib LD_LIBRARY_PATH=/opt/hyhal/lib:"${LD_LIBRARY_PATH:-}" \
      setsid nohup python3 -m fluidckpt.live.agent --node "$node" --port "$port" \
      >"$logdir/agent_$node.log" 2>&1 </dev/null &
    sleep 2
    if pgrep -f "$PATTERN" >/dev/null; then
      echo "started $(pgrep -f "$PATTERN" | tr '\n' ' ')"
    else
      echo "FAILED"; tail -20 "$logdir/agent_$node.log"
    fi
    ;;
  *) echo "usage: $0 start|stop" >&2; exit 2 ;;
esac
