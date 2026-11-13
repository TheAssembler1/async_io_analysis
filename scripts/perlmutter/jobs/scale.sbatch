#!/bin/bash
#SBATCH --account=m2621
#SBATCH -C cpu
#SBATCH -q regular
#SBATCH -t 02:30:00
#SBATCH -J pdc_scale
#SBATCH --exclusive
#SBATCH --network=job_vni
# Scaling: PDC over ofi+cxi, 32 clients + 2 servers per node, every kernel,
# sync and async, every client writing to the next node's servers.
#   sbatch -N 4 jobs/scale.sbatch       (submit_all.sh does 1, 2, 4, 8)
# net and halo are the network-bound kernels: net sends 8 MiB per rank to the
# next node, halo is a 3-D halo exchange. Both share the NIC with PDC's
# transfers once there is more than one node.
export TRANSPORTS=${TRANSPORTS:-cxi} TARGETS=${TARGETS:-remote} PLACEMENTS=${PLACEMENTS:-shared}
export KERNELS=${KERNELS:-"sleep spin dgemm stream halo net"} MODES=${MODES:-"sync async"}
# sbatch runs a copy of this file; find scripts/perlmutter from where it was submitted.
for PM_HERE in "${PM_SCRIPTS:-}" "$SLURM_SUBMIT_DIR" "$SLURM_SUBMIT_DIR/scripts/perlmutter" "$SLURM_SUBMIT_DIR/.."; do
  [ -f "$PM_HERE/common.sh" ] && break
done
[ -f "$PM_HERE/common.sh" ] || { echo "scripts/perlmutter not found: submit from the repo root or set PM_SCRIPTS" >&2; exit 1; }
export PM_SCRIPTS=$(cd "$PM_HERE" && pwd)
exec "$PM_SCRIPTS/matrix.sh"
