#!/bin/bash
#SBATCH --account=m2621
#SBATCH -C cpu
#SBATCH -q regular
#SBATCH -t 03:00:00
#SBATCH -J pdc_placement
#SBATCH --exclusive
#SBATCH --network=job_vni
# Where PDC's progress thread runs: on the rank's hardware thread (shared, PDC
# as deployed), on its SMT sibling (smt), or on a second core (reserved).
#   sbatch -N 2 jobs/placement.sbatch
export TRANSPORTS=${TRANSPORTS:-cxi} TARGETS=${TARGETS:-remote} PLACEMENTS=${PLACEMENTS:-"shared smt reserved"}
export KERNELS=${KERNELS:-"sleep spin dgemm stream halo net"} MODES=${MODES:-"sync async"}
# sbatch runs a copy of this file; find scripts/perlmutter from where it was submitted.
for PM_HERE in "${PM_SCRIPTS:-}" "$SLURM_SUBMIT_DIR" "$SLURM_SUBMIT_DIR/scripts/perlmutter" "$SLURM_SUBMIT_DIR/.."; do
  [ -f "$PM_HERE/common.sh" ] && break
done
[ -f "$PM_HERE/common.sh" ] || { echo "scripts/perlmutter not found: submit from the repo root or set PM_SCRIPTS" >&2; exit 1; }
export PM_SCRIPTS=$(cd "$PM_HERE" && pwd)
exec "$PM_SCRIPTS/matrix.sh"
