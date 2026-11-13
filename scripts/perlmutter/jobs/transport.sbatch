#!/bin/bash
#SBATCH --account=m2621
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
# sbatch runs a copy of this file; find scripts/perlmutter from where it was submitted.
for PM_HERE in "${PM_SCRIPTS:-}" "$SLURM_SUBMIT_DIR" "$SLURM_SUBMIT_DIR/scripts/perlmutter" "$SLURM_SUBMIT_DIR/.."; do
  [ -f "$PM_HERE/common.sh" ] && break
done
[ -f "$PM_HERE/common.sh" ] || { echo "scripts/perlmutter not found: submit from the repo root or set PM_SCRIPTS" >&2; exit 1; }
export PM_SCRIPTS=$(cd "$PM_HERE" && pwd)
exec "$PM_SCRIPTS/matrix.sh"
