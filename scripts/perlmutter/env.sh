# Source on Perlmutter (login or compute node) before building or running.
# Override any of the paths below by exporting them first.
module load PrgEnv-gnu cray-mpich cray-libsci cmake > /dev/null 2>&1

PM_HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
export AIO_ROOT=${AIO_ROOT:-$(cd "$PM_HERE/../.." && pwd)}
export PM_PREFIX=${PM_PREFIX:-$PSCRATCH/aio_pm}       # sources, builds, installs, binaries
export MERCURY_DIR=${MERCURY_DIR:-$PM_PREFIX/install/mercury}
export PDC_DIR=${PDC_DIR:-$PM_PREFIX/install/pdc}
export PM_BIN=${PM_BIN:-$PM_PREFIX/bin}               # aio_bench_pdc, progress_test
export PM_RUNDIR=${PM_RUNDIR:-$PSCRATCH/aio_runs}     # PDC_TMPDIR / PDC_DATA_LOC per run (Lustre)
export PM_RESULTS=${PM_RESULTS:-$AIO_ROOT/results/perlmutter}

export LD_LIBRARY_PATH=$PDC_DIR/lib:$MERCURY_DIR/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
export PATH=$PDC_DIR/bin:$MERCURY_DIR/bin:$PATH

# aio_bench asks for MPI_THREAD_MULTIPLE; Cray MPICH only grants it with this.
export MPICH_MAX_THREAD_SAFETY=multiple

# The laptop setup (pdc_env.sh) pins HG_HOST to 127.0.0.1. Here PDC picks the
# endpoint itself: cxi0:<pid> for ofi+cxi, or HG_HOST from hsn_host.sh for ofi+tcp.
unset HG_HOST
export HG_TRANSPORT=${HG_TRANSPORT:-ofi+cxi}
