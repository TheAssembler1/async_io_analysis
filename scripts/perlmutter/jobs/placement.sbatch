#!/bin/bash
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
exec "${PM_SCRIPTS:-$SLURM_SUBMIT_DIR}/matrix.sh"
