# Source on Tuolumne (login or compute node) before building or running.
# Override any of the paths below by exporting them first.
if command -v module > /dev/null 2>&1; then
  module load cmake > /dev/null 2>&1 || true
  module is-loaded cray-mpich > /dev/null 2>&1 || module load cray-mpich > /dev/null 2>&1 || true
fi

TUO_HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
export AIO_ROOT=${AIO_ROOT:-$(cd "$TUO_HERE/../.." && pwd)}
# $HOME and /usr/workspace are NFS; builds and run directories go to Lustre.
export TUO_PREFIX=${TUO_PREFIX:-/p/lustre5/$USER/aio_tuo}
export TUO_DEPS=${TUO_DEPS:-$TUO_PREFIX/install}        # libfabric, Mercury, PDC
export TUO_BIN=${TUO_BIN:-$TUO_PREFIX/bin}             # aio_bench_pdc, progress_test
export TUO_RUNDIR=${TUO_RUNDIR:-$TUO_PREFIX/runs}
export TUO_RESULTS=${TUO_RESULTS:-$AIO_ROOT/results/tuolumne}
export LIBFABRIC_DIR=$TUO_DEPS MERCURY_DIR=$TUO_DEPS PDC_DIR=$TUO_DEPS

export LD_LIBRARY_PATH=$TUO_DEPS/lib:$TUO_DEPS/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
export PKG_CONFIG_PATH=$TUO_DEPS/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}
export PATH=$TUO_DEPS/bin:$PATH

# Single-node runs over TCP loopback need no Slingshot/CXI auth-key setup.
export HG_TRANSPORT=${HG_TRANSPORT:-ofi+tcp}
export HG_HOST=${HG_HOST:-127.0.0.1}
