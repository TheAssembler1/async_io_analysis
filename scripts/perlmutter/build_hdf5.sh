#!/bin/bash
# Build the stack for the traced HDF5 runs on Perlmutter (run on a login node):
#   build_hdf5.sh [argobots] [hdf5] [volasync] [dftracer] [bench]    default: all five
# Everything goes to $PM_H5_DIR, the benchmark to $PM_H5_BENCH (see hdf5_env.sh).
#
# HDF5 1.14.6 is built parallel and thread-safe, which the async VOL requires and
# cray-hdf5-parallel is not. vol-async gets the same 1 ms wait patch as the paper's
# build (scripts/local/vol-async-wait.patch). DFTracer is built with MPI and HDF5
# interception against this HDF5 and Cray MPICH; it has no release tags, so
# DFT_REF pins a develop commit.
set -euo pipefail
source "$(dirname "$0")/hdf5_env.sh"
CC=${CC:-cc} CXX=${CXX:-CC}
ARCH=${ARCH:--march=znver3}
ABT_VERSION=${ABT_VERSION:-1.2}
HDF5_VERSION=${HDF5_VERSION:-1.14.6}
VOL_REF=${VOL_REF:-90fbba931aac7ca351833084f53a94e4d5e459b3}
DFT_REPO=${DFT_REPO:-https://github.com/llnl/dftracer.git}
DFT_REF=${DFT_REF:-89026e72ee141d8e22a5848e1b5fba6a1e834450}
J=${J:-16}
SRC=$PM_PREFIX/src
mkdir -p "$SRC" "$PM_H5_DIR" "$(dirname "$PM_H5_BENCH")"
echo "stack -> $PM_H5_DIR, CC=$CC CXX=$CXX"

fetch() { [ -s "$SRC/$2" ] || curl -fsSL -o "$SRC/$2" "$1"; }

build_argobots() {
  fetch "https://github.com/pmodels/argobots/releases/download/v$ABT_VERSION/argobots-$ABT_VERSION.tar.gz" argobots.tar.gz
  rm -rf "$SRC/argobots-$ABT_VERSION" && tar xf "$SRC/argobots.tar.gz" -C "$SRC"
  (cd "$SRC/argobots-$ABT_VERSION" && ./configure -q CC="$CC" --prefix="$PM_H5_DIR" && make -s -j"$J" && make -s install)
}

build_hdf5() {
  fetch "https://github.com/HDFGroup/hdf5/releases/download/hdf5_$HDF5_VERSION/hdf5-$HDF5_VERSION.tar.gz" hdf5.tar.gz
  rm -rf "$SRC/hdf5-$HDF5_VERSION" "$SRC/build-hdf5" && tar xf "$SRC/hdf5.tar.gz" -C "$SRC"
  cmake -S "$SRC/hdf5-$HDF5_VERSION" -B "$SRC/build-hdf5" -DCMAKE_C_COMPILER="$CC" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$PM_H5_DIR" -DBUILD_SHARED_LIBS=ON -DBUILD_STATIC_LIBS=OFF \
    -DHDF5_ENABLE_PARALLEL=ON -DHDF5_ENABLE_THREADSAFE=ON -DALLOW_UNSUPPORTED=ON \
    -DHDF5_BUILD_CPP_LIB=OFF -DHDF5_BUILD_FORTRAN=OFF -DHDF5_BUILD_JAVA=OFF -DHDF5_BUILD_HL_LIB=OFF \
    -DBUILD_TESTING=OFF -DHDF5_BUILD_EXAMPLES=OFF -DHDF5_BUILD_TOOLS=ON \
    -DHDF5_ENABLE_Z_LIB_SUPPORT=OFF -DHDF5_ENABLE_SZIP_SUPPORT=OFF
  cmake --build "$SRC/build-hdf5" -j"$J"
  cmake --install "$SRC/build-hdf5"
  grep -q "Threadsafety: ON" "$PM_H5_DIR/lib/libhdf5.settings" &&
    grep -q "Parallel HDF5: ON" "$PM_H5_DIR/lib/libhdf5.settings" ||
    { echo "HDF5 is not parallel and thread-safe" >&2; exit 1; }
}

build_volasync() {
  [ -d "$SRC/vol-async" ] || git clone -q https://github.com/hpc-io/vol-async.git "$SRC/vol-async"
  git -C "$SRC/vol-async" fetch -q origin
  git -C "$SRC/vol-async" checkout -q -f "$VOL_REF"
  git -C "$SRC/vol-async" apply "$AIO_ROOT/scripts/local/vol-async-wait.patch"
  rm -rf "$SRC/build-volasync"
  cmake -S "$SRC/vol-async" -B "$SRC/build-volasync" -DCMAKE_C_COMPILER="$CC" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$PM_H5_DIR" -DCMAKE_PREFIX_PATH="$PM_H5_DIR" -DHDF5_DIR="$PM_H5_DIR/cmake" \
    -DABT_DIR="$PM_H5_DIR" -DENABLE_WRITE_MEMCPY=ON
  cmake --build "$SRC/build-volasync" -j"$J"
  cmake --install "$SRC/build-volasync"
  ls "$PM_H5_DIR"/lib/libh5async.so > /dev/null
}

# Two CMake passes, as DFTracer's docs describe: the first builds its
# dependencies (brahma, gotcha, ...) into the prefix, the second DFTracer.
build_dftracer() {
  [ -d "$SRC/dftracer" ] || git clone -q "$DFT_REPO" "$SRC/dftracer"
  git -C "$SRC/dftracer" fetch -q origin && git -C "$SRC/dftracer" checkout -q "$DFT_REF"
  local bd=$SRC/build-dftracer lib a b c
  local opts=(-DCMAKE_C_COMPILER="$CC" -DCMAKE_CXX_COMPILER="$CXX" -DCMAKE_BUILD_TYPE=Release
    -DCMAKE_INSTALL_PREFIX="$PM_H5_DIR" -DCMAKE_PREFIX_PATH="$PM_H5_DIR"
    -DDFTRACER_BUILD_PYTHON_BINDINGS=OFF -DDFTRACER_ENABLE_MPI=ON -DDFTRACER_ENABLE_HDF5=ON
    -DHDF5_ROOT="$PM_H5_DIR" -DMPI_C_COMPILER="$(command -v "$CC")" -DMPI_CXX_COMPILER="$(command -v "$CXX")")
  if [ -n "${CRAY_MPICH_VERSION:-}" ]; then
    IFS=. read -r a b c <<< "$CRAY_MPICH_VERSION"
    opts+=(-DDFTRACER_MPI_IMPL=CRAYMPICH -DBRAHMA_MPI_VERSION=$((a * 100000 + b * 100 + ${c:-0})))
  fi
  rm -rf "$bd"
  cmake -S "$SRC/dftracer" -B "$bd" "${opts[@]}" -DDFTRACER_INSTALL_DEPENDENCIES=ON
  cmake --build "$bd" -j"$J"
  cmake -S "$SRC/dftracer" -B "$bd" "${opts[@]}" -DDFTRACER_INSTALL_DEPENDENCIES=OFF
  cmake --build "$bd" -j"$J"
  cmake --install "$bd"
  # A build can succeed with an interception missing; check for the calls we
  # need (gotcha wrappers, exported as C++ <call>_wrapper symbols).
  lib=$(ls "$PM_H5_DIR"/lib*/libdftracer_core.so | head -1)
  for f in MPI_File_write_at_all H5Dwrite pwrite; do
    nm -D --defined-only "$lib" | grep -q "${f}_wrapper" || { echo "$lib does not intercept $f" >&2; exit 1; }
  done
  ldd "$lib" | grep -E 'libmpi|libhdf5'
}

build_bench() {
  local bd=$PM_PREFIX/build-aio-h5 s=$AIO_ROOT/src
  mkdir -p "$bd"
  cat > "$bd/io_absent.c" << 'EOF'
#include "aio_bench.h"
#include <stdio.h>
static int absent(io_ctx *c)
{
    if (c->rank == 0) fprintf(stderr, "backend not built on this system\n");
    return -1;
}
static void nop_issue(io_ctx *c, int step, const void *buf) { (void)c; (void)step; (void)buf; }
static void nop(io_ctx *c) { (void)c; }
const io_backend io_adios2 = {"adios2", absent, nop_issue, nop, nop, NULL, NULL};
EOF
  # io_pdc.c without HAVE_PDC is the "unavailable" stub. BLAS comes from
  # cray-libsci through the cc wrapper; elsewhere set BLAS_LIBS.
  "$CC" -O2 $ARCH -Wall -I"$s" -I"$PM_H5_DIR/include" -o "$PM_H5_BENCH" \
    "$s"/aio_bench.c "$s"/trace.c "$s"/kernels.c "$s"/io_posix.c "$s"/io_mpiio.c "$s"/io_hdf5.c "$s"/io_pdc.c \
    "$s"/gpu_stub.c "$bd/io_absent.c" ${BENCH_EXTRA:-} \
    -L"$PM_H5_DIR/lib" -Wl,-rpath,"$PM_H5_DIR/lib" -lhdf5 ${BLAS_LIBS:-} -ldl -lrt -lpthread -lm
  "$CC" -O2 -Wall -shared -fPIC -o "$PM_H5_DIR/lib/libaio_monoclock.so" "$s/monoclock.c"
  env -u LD_LIBRARY_PATH ldd "$PM_H5_BENCH" | grep -q "$PM_H5_DIR/lib/libhdf5" ||
    { echo "$PM_H5_BENCH does not link $PM_H5_DIR/lib/libhdf5" >&2; exit 1; }
  echo "built $PM_H5_BENCH and $PM_H5_DIR/lib/libaio_monoclock.so"
}

for c in "${@:-argobots hdf5 volasync dftracer bench}"; do
  for x in $c; do echo "== $x"; "build_$x"; done
done
