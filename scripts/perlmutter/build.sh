#!/bin/bash
# Build Mercury, PDC, aio_bench_pdc and progress_test on Perlmutter (run on a login node).
#   build.sh [mercury] [pdc] [bench]       default: all three
# MERCURY_REF (v2.4.1; PDC's docs and the paper use 2.2.0, see below).
# PDC is cloned into deps/src/pdc and rebuilt from there, edits included
# (scripts/common/pdc.sh). The branch needs the ofi+cxi setup (cxi0:<pid>
# endpoints and the CXI service/VNI lookup): stock develop builds
# ofi+cxi://<hostname>:<port>, which the cxi provider rejects.
# libfabric is the system one (cray-libfabric, with the cxi and tcp providers):
# the docs' libfabric 1.18.0 has no cxi provider (upstream added it in 1.21).
# Mercury 2.2.0 builds against libfabric 2.x but cannot use cxi: it always sets
# av_attr.rx_ctx_bits, which the cxi provider rejects ("SEPs not supported"), so
# HG_Init fails. 2.4.0 does not compile against libfabric 2.x. 2.4.1 works and
# also takes the CXI service/VNI from Slurm's SLINGSHOT_* variables.
set -euo pipefail
source "$(dirname "$0")/env.sh"
MERCURY_REF=${MERCURY_REF:-v2.4.1}
source "$AIO_ROOT/scripts/common/pdc.sh"
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
    -DNA_USE_OFI=ON -DNA_USE_SM=OFF
  cmake --build "$SRC/build-mercury" -j"$J"
  rm -rf "$MERCURY_DIR" && cmake --install "$SRC/build-mercury"
  # PDC's pdc_private.h includes mercury_list.h (none of its macros are used),
  # which Mercury stopped shipping after 2.3. Install the 2.3.1 copy.
  [ -e "$MERCURY_DIR/include/mercury_list.h" ] ||
    git -C "$SRC/mercury" show v2.3.1:src/util/mercury_list.h > "$MERCURY_DIR/include/mercury_list.h"
}

build_pdc() {
  pdc_build "$SRC/build-pdc" "$PDC_DIR" -DCMAKE_C_COMPILER=cc \
    -DMERCURY_DIR="$MERCURY_DIR/share/cmake/mercury" -DCMAKE_PREFIX_PATH="$MERCURY_DIR"
  grep -q "^MERCURY_DIR:[A-Z]*=$MERCURY_DIR/" "$SRC/build-pdc/CMakeCache.txt" ||
    { echo "PDC configured against $(grep '^MERCURY_DIR:' "$SRC/build-pdc/CMakeCache.txt")" >&2; exit 1; }
  check_mercury "$PDC_DIR/bin/pdc_server"
  grep -qs PDC_get_default_cxi_endpoint "$PDC_SRC/src/server/pdc_server.c" || [ "$HG_TRANSPORT" != ofi+cxi ] ||
    echo "warning: this PDC's server has no cxi0:<pid> endpoint setup; ofi+cxi runs will fail (see top of build.sh)" >&2
  echo "PDC -> $PDC_DIR"
}

build_bench() {
  local s=$AIO_ROOT/src api=
  # HDF5 and ADIOS2 are not needed for the PDC runs; io_absent.c stands in for them.
  grep -rqs PDCobj_create_coll "$PDC_DIR/include" && api=-DPDC_COLL_API
  cc -O2 -march=znver3 -Wall -I"$s" -DHAVE_PDC $api -I"$PDC_DIR/include" -I"$MERCURY_DIR/include" \
    -o "$PM_BIN/aio_bench_pdc" "$s"/aio_bench.c "$s"/trace.c "$s"/kernels.c "$s"/io_posix.c "$s"/io_mpiio.c \
    "$s"/io_pdc.c "$s"/gpu_stub.c "$s"/io_absent.c \
    -L"$PDC_DIR/lib" -Wl,-rpath,"$PDC_DIR/lib" -L"$MERCURY_DIR/lib" -Wl,-rpath,"$MERCURY_DIR/lib" \
    -lpdc -lpdc_commons -lmercury -lrt -lpthread -lm
  cc -O2 -march=znver3 -Wall -I"$MERCURY_DIR/include" -o "$PM_BIN/progress_test" \
    "$AIO_ROOT/src/progress_test/progress_test.c" \
    -L"$MERCURY_DIR/lib" -Wl,-rpath,"$MERCURY_DIR/lib" -lmercury -lna -lmercury_util -lpthread -lm
  check_mercury "$PM_BIN/aio_bench_pdc"
  check_mercury "$PM_BIN/progress_test"
  echo "built $PM_BIN/aio_bench_pdc ${api:+(collective API)} and $PM_BIN/progress_test"
}

for c in "${@:-mercury pdc bench}"; do
  for x in $c; do "build_$x"; done
done
