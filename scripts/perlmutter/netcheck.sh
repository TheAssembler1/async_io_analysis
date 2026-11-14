#!/bin/bash
# Does the client step start next to a running server step, and where do the
# server tasks land? Run inside a 2-node allocation:
#   salloc -N 2 -C cpu -q interactive -t 20 -A m2621 --network=job_vni
#   scripts/perlmutter/netcheck.sh
# Concurrent steps fail with "Error configuring interconnect" when their CPU
# requests add up to more than a node (the Slingshot plugin sizes each step's
# NIC reservation by its CPUs), so the server step asks for 2 CPUs per task and
# relies on block:cyclic placement instead of CPU masks.
here=$(cd "$(dirname "$0")" && pwd)
cmask=$(python3 "$here/cpumask.py" client shared 32)
out=$(mktemp -d)

try() { # try LABEL "SERVER SRUN ARGS" "CLIENT SRUN ARGS"
  local sp
  srun --overlap $2 bash -c 'echo "server $SLURM_PROCID on $(hostname): $(grep Cpus_allowed_list /proc/self/status | cut -f2)"; sleep 60' \
    > "$out/server" 2>&1 &
  sp=$!
  sleep 5
  if ! kill -0 $sp 2> /dev/null; then echo "FAIL  $1: server step did not start: $(head -2 "$out/server")"; return; fi
  if timeout 60 srun --overlap $3 bash -c '[ "$SLURM_LOCALID" = 0 ] || [ "$SLURM_LOCALID" = 31 ] &&
      echo "client $SLURM_PROCID local $SLURM_LOCALID: $(grep Cpus_allowed_list /proc/self/status | cut -f2)"; true' \
      > "$out/client" 2> "$out/err"; then
    echo "ok    $1"
  else
    echo "FAIL  $1: $(grep -m1 -o 'launch failed: .*\|error: .*' "$out/err")"
  fi
  sort "$out/server" | sed 's/^/        /'
  sort -k2 -n "$out/client" 2> /dev/null | head -4 | sed 's/^/        /'
  kill -TERM $sp 2> /dev/null; sleep 1; kill -KILL $sp 2> /dev/null; wait $sp 2> /dev/null
}

try "server -c 2 block:cyclic, client 32/node -c 8 masks (new layout)" \
    "-N 2 --ntasks-per-node=2 -c 2 -m block:cyclic --cpu-bind=cores" \
    "-N 2 --ntasks-per-node=32 -c 8 --cpu-bind=mask_cpu:$cmask"
try "same, client 32/node -c 4 masks" \
    "-N 2 --ntasks-per-node=2 -c 2 -m block:cyclic --cpu-bind=cores" \
    "-N 2 --ntasks-per-node=32 -c 4 --cpu-bind=mask_cpu:$cmask"
rm -rf "$out"
