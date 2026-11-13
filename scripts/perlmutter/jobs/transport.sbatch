#!/bin/bash
#SBATCH -C cpu
#SBATCH -q regular
#SBATCH -t 03:00:00
#SBATCH -J pdc_transport
#SBATCH --exclusive
#SBATCH --network=job_vni
# ofi+cxi (NIC RDMA) against ofi+tcp over the same Slingshot NIC (hsn0), where
# the server's pull is emulated with socket copies on both sides, as on the
# single node used in the paper.
#   sbatch -N 2 jobs/transport.sbatch
export TRANSPORTS=${TRANSPORTS:-"cxi tcp"} TARGETS=${TARGETS:-remote} PLACEMENTS=${PLACEMENTS:-shared}
export KERNELS=${KERNELS:-"sleep spin stream halo net"} MODES=${MODES:-"sync async"}
exec "${PM_SCRIPTS:-$SLURM_SUBMIT_DIR}/matrix.sh"
