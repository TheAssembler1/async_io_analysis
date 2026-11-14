#!/bin/bash
# Which layout lets a client step start next to a running server step without
# "Error configuring interconnect"? Run inside a 2-node allocation:
#   salloc -N 2 -C cpu -q interactive -t 20 -A m2621 --network=job_vni
#   scripts/perlmutter/netcheck.sh
# Every case starts a server-shaped step running `sleep`, then a client-shaped
# step running `true`, and prints ok/FAIL.
here=$(cd "$(dirname "$0")" && pwd)
cmask=$(python3 "$here/cpumask.py" client shared 32)
smask=$(python3 "$here/cpumask.py" server 2)
small=def_acs=1,def_les=4,def_ptes=1,def_txqs=1,def_tgqs=1,def_eqs=1,def_cts=1,def_tles=0

echo "== Slingshot settings"
scontrol show config | grep -i -E "SwitchParameters|SwitchType"
echo

try() { # try LABEL "SERVER SRUN ARGS" "CLIENT SRUN ARGS"
  local sp
  srun --overlap $2 sleep 60 > /dev/null 2>&1 &
  sp=$!
  sleep 5
  if ! kill -0 $sp 2> /dev/null; then echo "FAIL  $1: server step did not start"; return; fi
  if timeout 60 srun --overlap $3 true > /dev/null 2> /tmp/netcheck.$$; then
    echo "ok    $1"
  else
    echo "FAIL  $1: $(grep -m1 -o 'launch failed: .*\|error: .*' /tmp/netcheck.$$)"
  fi
  kill -TERM $sp 2> /dev/null; sleep 1; kill -KILL $sp 2> /dev/null; wait $sp 2> /dev/null
  rm -f /tmp/netcheck.$$
}

S="-N 2 --ntasks-per-node=2"
C="-N 2 --ntasks-per-node=32"
try "progress_test shape: 1-task server, 8-task client, 1 node" \
    "-N 1 -n 1 -c 256" "-N 1 -n 8 -c 32"
try "server -c 128, client 8/node -c 8" "$S -c 128" "-N 2 --ntasks-per-node=8 -c 8"
try "server -c 2, client 32/node -c 2" "$S -c 2" "$C -c 2"
try "server -c 1, client 32/node -c 1" "$S -c 1" "$C -c 1"
try "small defs, server -c 2, client -c 2" \
    "--network=job_vni,$small $S -c 2" "--network=job_vni,$small $C -c 2"
try "small defs, server -c 128 masks, client -c 8 masks (our layout)" \
    "--network=job_vni,$small $S -c 128 --cpu-bind=mask_cpu:$smask" \
    "--network=job_vni,$small $C -c 8 --cpu-bind=mask_cpu:$cmask"
try "server -c 2 masks, client -c 2 masks" \
    "$S -c 2 --cpu-bind=mask_cpu:$smask" "$C -c 2 --cpu-bind=mask_cpu:$cmask"
