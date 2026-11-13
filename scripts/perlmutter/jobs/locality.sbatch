#!/bin/bash
#SBATCH -C cpu
#SBATCH -q regular
#SBATCH -t 03:00:00
#SBATCH -J pdc_locality
#SBATCH --exclusive
#SBATCH --network=job_vni
# Clients writing to the servers on their own node (local, PDC's default
# mapping for this layout) or on the next node (remote, all traffic on the
# network). Needs at least 2 nodes.
#   sbatch -N 4 jobs/locality.sbatch
export TRANSPORTS=${TRANSPORTS:-cxi} TARGETS=${TARGETS:-"local remote"} PLACEMENTS=${PLACEMENTS:-shared}
export KERNELS=${KERNELS:-"sleep spin stream halo net"} MODES=${MODES:-"sync async"}
exec "${PM_SCRIPTS:-$SLURM_SUBMIT_DIR}/matrix.sh"
