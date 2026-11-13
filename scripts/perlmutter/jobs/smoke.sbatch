#!/bin/bash
#SBATCH -C cpu
#SBATCH -q debug
#SBATCH -t 00:30:00
#SBATCH -J pdc_smoke
#SBATCH -N 2
#SBATCH --exclusive
#SBATCH --network=job_vni
# Run this first: checks the network setup and runs every path once with small
# transfers. Rows go to $PM_RESULTS/pdc_smoke_n2.csv and knobs_smoke.csv.
#   sbatch jobs/smoke.sbatch
set -u
PM_HERE=${PM_SCRIPTS:-$SLURM_SUBMIT_DIR}
export MB=${MB:-32} STEPS=${STEPS:-2} BASE_REPS=1 CASE_TIMEOUT=300
source "$PM_HERE/common.sh"

echo "== environment"
module list 2>&1
echo "NERSC_HOST=${NERSC_HOST:-} nodes=$SLURM_JOB_NODELIST PDC=$PDC_DIR"
srun --overlap -N 2 -n 2 bash -c 'echo "$(hostname): SLINGSHOT_VNIS=${SLINGSHOT_VNIS:-} SLINGSHOT_SVC_IDS=${SLINGSHOT_SVC_IDS:-} hsn0=$(ip -4 -o addr show hsn0 | awk "{print \$4}")"'
fi_info -p cxi 2>&1 | grep -m3 "provider\|domain\|fabric"
fi_info -p tcp 2>&1 | grep -m3 "provider\|domain\|fabric"
srun --overlap -N 1 -n 1 cxi_service -v list 2>&1 | head -40
echo "server masks: $SRV_MASK"
echo "client masks (shared): $(python3 "$PM_HERE/cpumask.py" client shared "$CPN" | cut -c1-120)..."

echo "== PDC"
for TRANSPORT in cxi tcp; do
  for TARGET in local remote; do
    PLACEMENT=shared
    pdc_case sync none 0
    pdc_case async spin 0.5
    pdc_case async net 0.5
  done
done
PLACEMENT=smt TARGET=remote TRANSPORT=cxi pdc_case async spin 0.5
PLACEMENT=reserved TARGET=remote TRANSPORT=cxi pdc_case async spin 0.5

echo "== progress_test"
GRID=0 REPS=1 KERNELS=spin NP=8 MB=32 HGS="ofi+cxi ofi+tcp" LOCATIONS="remote local" \
  CSV=$PM_RESULTS/knobs_smoke.csv bash "$PM_HERE/jobs/knobs.sbatch"

echo "== results"
column -s, -t < "$CSV" | cut -c1-200
column -s, -t < "$PM_RESULTS/knobs_smoke.csv" | cut -c1-200
