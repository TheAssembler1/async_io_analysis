#!/bin/bash
#SBATCH --account=m2621
#SBATCH -C cpu
#SBATCH -q regular
#SBATCH -t 03:00:00
#SBATCH -J hdf5_trace
#SBATCH -N 2
#SBATCH --exclusive
# HDF5 with each kernel, sync (native VOL) and async (async VOL), one fsync
# at the end, with one hardware thread per rank (shared) and with two cores
# per rank, the async VOL's thread pinned to the second (iocore); settings in
# hdf5.sh. The first repetition of each case is traced with DFTracer. Build
# first with build_hdf5.sh.
#   sbatch jobs/hdf5_trace.sbatch
#   sbatch --export=ALL,FLUSHES="endflush flush",KERNELS="sleep dgemm" jobs/hdf5_trace.sbatch
#   sbatch -q debug -t 00:30:00 --export=ALL,KERNELS=dgemm,REPS=1,MB=16,STEPS=3 jobs/hdf5_trace.sbatch   (try first)
# sbatch runs a copy of this file; find scripts/perlmutter from where it was submitted.
for PM_HERE in "${PM_SCRIPTS:-}" "$SLURM_SUBMIT_DIR" "$SLURM_SUBMIT_DIR/scripts/perlmutter" "$SLURM_SUBMIT_DIR/.."; do
  [ -f "$PM_HERE/hdf5.sh" ] && break
done
[ -f "$PM_HERE/hdf5.sh" ] || { echo "scripts/perlmutter not found: submit from the repo root or set PM_SCRIPTS" >&2; exit 1; }
export PM_SCRIPTS=$(cd "$PM_HERE" && pwd)
exec "$PM_SCRIPTS/hdf5.sh"
