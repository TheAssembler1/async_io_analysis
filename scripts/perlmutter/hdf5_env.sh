# Source for the HDF5 runs (build_hdf5.sh, hdf5.sh, jobs/hdf5_trace.sbatch).
# Kept apart from env.sh so the PDC runs do not pick up this HDF5.
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
# Darshan is loaded by default and would intercept the same calls as DFTracer.
# cray-hdf5 is not thread-safe, which the async VOL needs, so HDF5 is built here.
module unload darshan cray-hdf5 cray-hdf5-parallel > /dev/null 2>&1

export PM_H5_DIR=${PM_H5_DIR:-$PM_PREFIX/install/h5}  # Argobots, HDF5, vol-async, DFTracer
export LD_LIBRARY_PATH=$PM_H5_DIR/lib:$PM_H5_DIR/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
export PATH=$PM_H5_DIR/bin:$PATH
export HDF5_PLUGIN_PATH=$PM_H5_DIR/lib
export PM_H5_BENCH=${PM_H5_BENCH:-$PM_BIN/aio_bench_h5}
DFT_PRELOAD=$(ls "$PM_H5_DIR"/lib*/libdftracer_preload.so 2> /dev/null | head -1 || true)
export DFT_PRELOAD
export AIO_MONOCLOCK=$PM_H5_DIR/lib/libaio_monoclock.so  # see src/monoclock.c
# dgemm is libsci's; one thread per rank, as the placements assume.
export OMP_NUM_THREADS=1
