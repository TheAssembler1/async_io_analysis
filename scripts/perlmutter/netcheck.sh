#!/bin/bash
# Which srun layout gets past "Error configuring interconnect"? Run inside a
# 2-node allocation:
#   salloc -N 2 -C cpu -q interactive -t 20 -A m2621 --network=job_vni
#   scripts/perlmutter/netcheck.sh
# Each line launches `true` in a client-shaped step (32 tasks/node, 2 nodes),
# some while a server-shaped step (2 tasks/node) is running, and prints ok/FAIL.
here=$(cd "$(dirname "$0")" && pwd)
cmask=$(python3 "$here/cpumask.py" client shared 32)
smask=$(python3 "$here/cpumask.py" server 2)

try() { # try LABEL SERVER(0|1) EXTRA... -- CLIENT_SRUN_ARGS...
  local label=$1 server=$2 extra=() sp=; shift 2
  while [ "$1" != -- ]; do extra+=("$1"); shift; done; shift
  if [ "$server" = 1 ]; then
    srun --overlap "${extra[@]}" -N 2 --ntasks-per-node=2 -c 128 --cpu-bind=mask_cpu:"$smask" sleep 60 &
    sp=$!; sleep 5
  fi
  if timeout 60 srun --overlap "${extra[@]}" -N 2 "$@" true 2> /tmp/netcheck.$$; then
    echo "ok    $label"
  else
    echo "FAIL  $label: $(grep -m1 -o 'launch failed: .*\|error: .*' /tmp/netcheck.$$)"
  fi
  [ -n "$sp" ] && { kill -TERM $sp 2> /dev/null; wait $sp 2> /dev/null; }
  rm -f /tmp/netcheck.$$
}

try "32/node alone, masks"                 0 -- --ntasks-per-node=32 -c 8 --cpu-bind=mask_cpu:"$cmask"
try "32/node alone, no masks"              0 -- --ntasks-per-node=32 -c 8
try "32/node + server step, masks"         1 -- --ntasks-per-node=32 -c 8 --cpu-bind=mask_cpu:"$cmask"
try "32/node + server step, no masks"      1 -- --ntasks-per-node=32 -c 8
try "16/node + server step, no masks"      1 -- --ntasks-per-node=16 -c 16
try "32/node + server step, def_tles=0"    1 --network=def_tles=0 -- --ntasks-per-node=32 -c 8 --cpu-bind=mask_cpu:"$cmask"
try "32/node + server step, single_node_vni" 1 --network=single_node_vni,job_vni -- --ntasks-per-node=32 -c 8 --cpu-bind=mask_cpu:"$cmask"
