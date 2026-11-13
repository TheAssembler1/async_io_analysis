#!/bin/bash
# Build Mercury, PDC, aio_bench_pdc and progress_test on Perlmutter (run on a login node).
#   build.sh [mercury] [pdc] [bench]       default: all three
# MERCURY_REF (v2.4.1; 2.4.0 does not build against libfabric 2.x), PDC_REPO, PDC_REF: which sources to build. The default PDC
# branch is the one that sets up ofi+cxi on Perlmutter (cxi0:<pid> endpoints and
# the CXI service/VNI lookup); stock PDC builds ofi+cxi://<hostname>:<port>.
# libfabric is the system one (cray-libfabric, with the cxi and tcp providers).
set -euo pipefail
source "$(dirname "$0")/env.sh"
MERCURY_REF=${MERCURY_REF:-v2.4.1}
PDC_REPO=${PDC_REPO:-https://github.com/hpc-io/pdc.git}
PDC_REF=${PDC_REF:-fix/perlmutter-cxi-defaults}
J=${J:-16}
SRC=$PM_PREFIX/src
mkdir -p "$SRC" "$PM_BIN"

echo "Mercury -> $MERCURY_DIR"
echo "PDC     -> $PDC_DIR"
pkg-config --exists libfabric || { echo "libfabric not found by pkg-config (module load cray-libfabric?)" >&2; exit 1; }
echo "libfabric $(pkg-config --modversion libfabric) from $(pkg-config --variable=prefix libfabric)"

# Fail if a binary resolves libmercury from anywhere but $MERCURY_DIR (without
# LD_LIBRARY_PATH, which env.sh points at the right one and would hide a bad rpath).
check_mercury() {
  local m
  m=$(env -u LD_LIBRARY_PATH ldd "$1" | awk '/libmercury\.so/ {print $3}')
  case $m in
    "$MERCURY_DIR"/*) echo "$(basename "$1") uses $m" ;;
    *) echo "$1 links libmercury from '$m', not $MERCURY_DIR" >&2; exit 1 ;;
  esac
}

build_mercury() {
  [ -d "$SRC/mercury" ] || git clone -q --recurse-submodules https://github.com/mercury-hpc/mercury.git "$SRC/mercury"
  git -C "$SRC/mercury" fetch -q --tags && git -C "$SRC/mercury" checkout -q "$MERCURY_REF"
  git -C "$SRC/mercury" submodule update -q --init --recursive
  rm -rf "$SRC/build-mercury"
  cmake -S "$SRC/mercury" -B "$SRC/build-mercury" -DCMAKE_C_COMPILER=cc -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$MERCURY_DIR" -DBUILD_SHARED_LIBS=ON \
    -DBUILD_TESTING=OFF -DMERCURY_USE_BOOST_PP=ON -DMERCURY_USE_CHECKSUMS=OFF \
    -DNA_USE_OFI=ON -DNA_USE_SM=ON
  cmake --build "$SRC/build-mercury" -j"$J" && cmake --install "$SRC/build-mercury"
}

build_pdc() {
  [ -d "$SRC/pdc" ] || git clone -q "$PDC_REPO" "$SRC/pdc"
  git -C "$SRC/pdc" fetch -q origin && git -C "$SRC/pdc" checkout -q "$PDC_REF"
  git -C "$SRC/pdc" pull -q --ff-only 2> /dev/null || true
  rm -rf "$SRC/build-pdc"
  # PDC_ENABLE_MULTITHREAD stays OFF (the default): busy-polling progress, as in the paper.
  cmake -S "$SRC/pdc" -B "$SRC/build-pdc" -DCMAKE_C_COMPILER=cc -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$PDC_DIR" -DBUILD_SHARED_LIBS=ON \
    -DBUILD_TESTING=OFF -DPDC_ENABLE_MPI=ON -DPDC_SERVER_CACHE=ON \
    -DMERCURY_DIR="$MERCURY_DIR/share/cmake/mercury" -DCMAKE_PREFIX_PATH="$MERCURY_DIR"
  cmake --build "$SRC/build-pdc" -j"$J" && cmake --install "$SRC/build-pdc"
  grep -q "^MERCURY_DIR:[A-Z]*=$MERCURY_DIR/" "$SRC/build-pdc/CMakeCache.txt" ||
    { echo "PDC configured against $(grep '^MERCURY_DIR:' "$SRC/build-pdc/CMakeCache.txt")" >&2; exit 1; }
  check_mercury "$PDC_DIR/bin/pdc_server"
  echo "PDC $(git -C "$SRC/pdc" describe --always --tags) -> $PDC_DIR"
}

build_bench() {
  local b=$PM_PREFIX/build-aio s=$AIO_ROOT/src api=
  mkdir -p "$b"
  # HDF5 and ADIOS2 are not needed for the PDC runs; leave their backends out.
  cat > "$b/io_absent.c" << 'EOF'
#include "aio_bench.h"
#include <stdio.h>
static int absent(io_ctx *c)
{
    if (c->rank == 0) fprintf(stderr, "backend not built on this system\n");
    return -1;
}
static void nop_issue(io_ctx *c, int step, const void *buf) { (void)c; (void)step; (void)buf; }
static void nop(io_ctx *c) { (void)c; }
const io_backend io_hdf5 = {"hdf5", absent, nop_issue, nop, nop, NULL, NULL};
const io_backend io_adios2 = {"adios2", absent, nop_issue, nop, nop, NULL, NULL};
EOF
  grep -rqs PDCobj_create_coll "$PDC_DIR/include" && api=-DPDC_COLL_API
  cc -O2 -march=znver3 -Wall -I"$s" -DHAVE_PDC $api -I"$PDC_DIR/include" -I"$MERCURY_DIR/include" \
    -o "$PM_BIN/aio_bench_pdc" "$s"/aio_bench.c "$s"/kernels.c "$s"/io_posix.c "$s"/io_mpiio.c \
    "$s"/io_pdc.c "$s"/gpu_stub.c "$b/io_absent.c" \
    -L"$PDC_DIR/lib" -Wl,-rpath,"$PDC_DIR/lib" -L"$MERCURY_DIR/lib" -Wl,-rpath,"$MERCURY_DIR/lib" \
    -lpdc -lpdc_commons -lmercury -lrt -lpthread -lm
  cc -O2 -march=znver3 -Wall -I"$MERCURY_DIR/include" -o "$PM_BIN/progress_test" \
    "$AIO_ROOT/experiments/progress_knobs/progress_test.c" \
    -L"$MERCURY_DIR/lib" -Wl,-rpath,"$MERCURY_DIR/lib" -lmercury -lna -lmercury_util -lpthread -lm
  check_mercury "$PM_BIN/aio_bench_pdc"
  check_mercury "$PM_BIN/progress_test"
  echo "built $PM_BIN/aio_bench_pdc ${api:+(collective API)} and $PM_BIN/progress_test"
}

for c in "${@:-mercury pdc bench}"; do
  for x in $c; do "build_$x"; done
done
