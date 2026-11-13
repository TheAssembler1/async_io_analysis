#!/usr/bin/env bash
# Sweep the progress-loop knobs. One fresh server per run, pinned to SERVER_CPUS;
# clients pinned per CLIENT_CPUS (one hardware thread each by default).
#   run_sweep.sh [out.csv]
# Environment: GRID (0), REPS (3), MB (256), STEPS (5), RATIO (1.25), KERNELS, NP (4),
#   SERVER_CPUS (0,1), CLIENT_CPUS ("2;4;6;8"), SMT_CPUS ("2,3;4,5;6,7;8,9")
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
source "${PDC_ENV:-$HOME/src/workspace/pdc_env.sh}" # Mercury/libfabric paths only
out=${1:-$here/results.csv}
REPS=${REPS:-3} MB=${MB:-256} STEPS=${STEPS:-5} RATIO=${RATIO:-1.25} NP=${NP:-4}
KERNELS=${KERNELS:-"sleep spin fma stream"}
SERVER_CPUS=${SERVER_CPUS:-0,1}
CLIENT_CPUS=${CLIENT_CPUS:-"2;4;6;8"}
SMT_CPUS=${SMT_CPUS:-"2,3;4,5;6,7;8,9"}
addr=$(mktemp -u)

# placement transport blocking timeout_ms sleep_us
configs=(
  "shared ofi+tcp 0 100 1000"     # PDC as shipped
  "shared ofi+tcp 0 10 1000"
  "shared ofi+tcp 0 1 1000"
  "shared ofi+tcp 0 0 1000"
  "shared ofi+tcp 0 0 100"
  "shared ofi+tcp 0 0 10000"
  "shared ofi+tcp 0 100 10000"
  "shared ofi+tcp 0 100 0"
  "shared ofi+tcp 1 100 1000"     # epoll; wakes up spuriously with tcp;ofi_rxm
  "shared ofi+sockets 0 100 1000"
  "shared ofi+sockets 1 100 1000" # blocks properly; provider thread moves data
  "smt ofi+tcp 0 100 1000"
  "smt ofi+tcp 0 0 1000"
  "smt ofi+sockets 1 100 1000"
)

# GRID=1: full timeout x sleep grid (tcp, busy, shared) instead of the list above
if [ "${GRID:-0}" = 1 ]; then
  configs=()
  for to in 0 1 10 100; do
    for sl in 0 100 1000 10000; do configs+=("shared ofi+tcp 0 $to $sl"); done
  done
fi

header_done=$([ -s "$out" ] && echo 1 || echo 0)

run() {
  local rep=$1 place=$2 hg=$3 blk=$4 to=$5 sl=$6 k=$7 cpus=$CLIENT_CPUS
  [ "$place" = smt ] && cpus=$SMT_CPUS
  rm -f "$addr"
  PT_HG=$hg taskset -c "$SERVER_CPUS" "$here/progress_test" server "$addr" 2> /dev/null &
  local sp=$!
  for _ in $(seq 100); do [ -s "$addr" ] && break; sleep 0.1; done
  local res
  res=$(PT_HG=$hg PT_CPUS="$cpus" PT_MB=$MB PT_STEPS=$STEPS PT_RATIO=$RATIO PT_KERNEL=$k \
        PT_BLOCKING=$blk PT_TIMEOUT_MS=$to PT_SLEEP_US=$sl \
        timeout 600 mpiexec -n "$NP" --bind-to none "$here/progress_test" client "$addr") || res=""
  kill "$sp" 2> /dev/null; wait "$sp" 2> /dev/null || true
  if [ -z "$res" ]; then echo "FAILED: $*" >&2; return; fi
  if [ "$header_done" = 0 ]; then echo "rep,placement,$(head -1 <<< "$res")" > "$out"; header_done=1; fi
  echo "$rep,$place,$(tail -1 <<< "$res")" >> "$out"
  echo "rep $rep $place $hg blk=$blk timeout=$to sleep=$sl $k: done" >&2
}

for rep in $(seq "$REPS"); do
  for c in "${configs[@]}"; do
    for k in $KERNELS; do run "$rep" $c "$k"; done
  done
done
rm -f "$addr"
