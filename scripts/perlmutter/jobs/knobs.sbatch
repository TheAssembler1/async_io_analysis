#!/bin/bash
#SBATCH --account=m2621
#SBATCH -C cpu
#SBATCH -q regular
#SBATCH -t 04:00:00
#SBATCH -J pt_knobs
#SBATCH -N 2
#SBATCH --exclusive
#SBATCH --network=job_vni
# Progress-loop knobs (experiments/progress_knobs, Mercury only) on Slingshot.
# One busy-polling server pulls from NP clients. remote: server on the first
# node, clients on the second, so every pull crosses the network; local: all
# on the first node. Same columns as the laptop results.csv plus location.
#   sbatch jobs/knobs.sbatch
# NP (32), MB (256), STEPS (5), RATIO (1.25), REPS (3), KERNELS, HGS (ofi+cxi),
# LOCATIONS (remote), GRID (1: timeout {0,1,10,100} x sleep {0,100,1000,10000} us)
set -u
PM_HERE=${PM_SCRIPTS:-$SLURM_SUBMIT_DIR}
source "$PM_HERE/env.sh"
NP=${NP:-32} MB=${MB:-256} STEPS=${STEPS:-5} RATIO=${RATIO:-1.25} REPS=${REPS:-3}
KERNELS=${KERNELS:-"sleep spin fma stream"}
HGS=${HGS:-ofi+cxi}
LOCATIONS=${LOCATIONS:-remote}
out=${CSV:-$PM_RESULTS/knobs.csv}
RUNS=$PM_RUNDIR/${SLURM_JOB_ID:-$$}
mkdir -p "$PM_RESULTS" "$RUNS"
mapfile -t nodes < <(scontrol show hostnames "$SLURM_JOB_NODELIST")
smask=$(python3 "$PM_HERE/cpumask.py" server 1)

# placement blocking timeout_ms sleep_us
configs=("shared 0 100 1000" "shared 1 100 1000" "smt 0 100 1000" "smt 0 0 1000" "reserved 0 100 1000")
if [ "${GRID:-1}" = 1 ]; then
  for to in 0 1 10 100; do
    for sl in 0 100 1000 10000; do
      [ "$to/$sl" = 100/1000 ] || configs+=("shared 0 $to $sl")
    done
  done
fi

seq=0
run() {
  local rep=$1 loc=$2 hg=$3 place=$4 blk=$5 to=$6 sl=$7 k=$8 addr res sp cnode=${nodes[0]}
  addr=$RUNS/addr.$((++seq))
  [ "$loc" = remote ] && cnode=${nodes[1]}
  PT_HG=$hg srun --overlap -N 1 -n 1 -w "${nodes[0]}" -c 256 --cpu-bind=mask_cpu:"$smask" \
    "$PM_HERE/hsn_host.sh" "$PM_HERE/cxi_env.sh" "$PM_BIN/progress_test" server "$addr" > "$addr.server.log" 2>&1 &
  sp=$!
  for _ in $(seq 600); do [ -s "$addr" ] && break; kill -0 $sp 2> /dev/null || break; sleep 0.1; done
  res=$(PT_HG=$hg PT_MB=$MB PT_STEPS=$STEPS PT_RATIO=$RATIO PT_KERNEL=$k \
        PT_BLOCKING=$blk PT_TIMEOUT_MS=$to PT_SLEEP_US=$sl \
        timeout 900 srun --overlap -N 1 -n "$NP" -w "$cnode" -c $((256 / NP)) \
        --cpu-bind=mask_cpu:"$(python3 "$PM_HERE/cpumask.py" client "$place" "$NP")" \
        "$PM_HERE/hsn_host.sh" "$PM_HERE/cxi_env.sh" "$PM_BIN/progress_test" client "$addr" 2> "$addr.client.log") || res=""
  kill -TERM $sp 2> /dev/null
  for _ in $(seq 40); do kill -0 $sp 2> /dev/null || break; sleep 0.5; done
  kill -KILL $sp 2> /dev/null; wait $sp 2> /dev/null
  if [ -z "$res" ]; then
    echo "$(date +%T) FAIL rep $rep $loc $hg $place blk=$blk to=$to sl=$sl $k, see $addr.*.log" >&2
    return
  fi
  [ -s "$out" ] || echo "rep,location,placement,$(head -1 <<< "$res")" > "$out"
  echo "$rep,$loc,$place,$(tail -1 <<< "$res")" >> "$out"
  echo "$(date +%T) ok rep $rep $loc $hg $place blk=$blk to=$to sl=$sl $k" >&2
  rm -f "$addr" "$addr".*.log
}

for rep in $(seq "$REPS"); do
  for loc in $LOCATIONS; do
    for hg in $HGS; do
      for c in "${configs[@]}"; do
        for k in $KERNELS; do run "$rep" "$loc" "$hg" $c "$k"; done
      done
    done
  done
done
