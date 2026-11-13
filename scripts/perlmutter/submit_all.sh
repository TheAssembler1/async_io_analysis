#!/bin/bash
# Submit the Perlmutter jobs. Run jobs/smoke.sbatch first and check its output.
#   ACCOUNT=m1234 ./submit_all.sh [job ...]
# jobs: scale placement transport locality knobs   (default: all five)
#   scale      NODE_COUNTS (1 2 4 8) nodes each
#   locality   NODE_COUNTS without 1
#   placement, transport  PAIR_NODES (2) nodes;  knobs  2 nodes
# Extra sbatch options go in SBATCH_OPTS, e.g. SBATCH_OPTS="-q premium".
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
source "$here/env.sh"
export PM_SCRIPTS=$here
NODE_COUNTS=${NODE_COUNTS:-"1 2 4 8"}
PAIR_NODES=${PAIR_NODES:-2}
mkdir -p "$PM_RESULTS/slurm"
[ -x "$PM_BIN/aio_bench_pdc" ] || { echo "run build.sh first ($PM_BIN/aio_bench_pdc missing)" >&2; exit 1; }

sub() { # sub NODES JOBFILE
  local id
  id=$(sbatch --parsable ${ACCOUNT:+-A "$ACCOUNT"} ${SBATCH_OPTS:-} -N "$1" \
    -o "$PM_RESULTS/slurm/%x-n$1-%j.out" "$here/jobs/$2.sbatch")
  echo "$2 on $1 node(s): job $id"
}

for job in ${@:-scale placement transport locality knobs}; do
  case $job in
    scale)     for n in $NODE_COUNTS; do sub "$n" scale; done ;;
    locality)  for n in $NODE_COUNTS; do [ "$n" -ge 2 ] && sub "$n" locality; done ;;
    placement | transport) sub "$PAIR_NODES" "$job" ;;
    knobs)     sub 2 knobs ;;
    smoke)     sub 2 smoke ;;
    *) echo "unknown job $job" >&2; exit 1 ;;
  esac
done
echo "logs: $PM_RESULTS/slurm, results: $PM_RESULTS/*.csv"
