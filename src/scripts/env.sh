# Source to set up the environment for aio_bench.
AIO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
export AIO_ROOT
export AIO_DEPS=$AIO_ROOT/deps/install
export PATH=$AIO_DEPS/bin:/usr/local/cuda/bin:$PATH
export LD_LIBRARY_PATH=$AIO_DEPS/lib:/usr/local/cuda/lib64:${LD_LIBRARY_PATH:-}
export HDF5_PLUGIN_PATH=$AIO_DEPS/lib
export AIO_BENCH=$AIO_ROOT/src/aio_bench
export AIO_SCRATCH=${AIO_SCRATCH:-/mnt/fast/nlewis/aio_scratch}
