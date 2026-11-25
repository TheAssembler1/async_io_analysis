#!/bin/bash
# Build libfabric, Mercury, PDC, aio_bench_pdc and progress_test on Tuolumne
# (login node, or inside `flux alloc -N1` for a faster build).
#   build.sh [libfabric] [mercury] [pdc] [bench]    default: all
# Versions follow PDC's docs (libfabric 1.18.0, Mercury 2.2.0). PDC comes from
# deps/src/pdc, cloned on the first build (branch: scripts/common/pdc.sh);
# after editing it, `build.sh pdc bench` rebuilds.
set -euo pipefail
source "$(dirname "$0")/env.sh"
source "$AIO_ROOT/scripts/common/pdc.sh"
J=${J:-32}
SRC=$TUO_PREFIX/src
mkdir -p "$SRC" "$TUO_BIN"
MPICC=${MPICC:-$(command -v mpicc || command -v cc)}

fetch() { [ -e "$SRC/$2" ] || curl -fsSL --retry 3 -o "$SRC/$2" "$1"; }

build_libfabric() {
  fetch https://github.com/ofiwg/libfabric/releases/download/v1.18.0/libfabric-1.18.0.tar.bz2 libfabric-1.18.0.tar.bz2
  rm -rf "$SRC/libfabric-1.18.0" && tar xjf "$SRC/libfabric-1.18.0.tar.bz2" -C "$SRC"
  (cd "$SRC/libfabric-1.18.0" && ./configure --prefix="$TUO_DEPS" CC="$MPICC" CFLAGS=-O2 \
    --enable-tcp=yes --enable-rxm=yes --enable-sockets=yes \
    --disable-verbs --disable-psm3 --disable-efa --disable-usnic --disable-opx &&
    make -j"$J" && make install)
}

build_mercury() {
  fetch https://github.com/mercury-hpc/mercury/releases/download/v2.2.0/mercury-2.2.0.tar.bz2 mercury-2.2.0.tar.bz2
  rm -rf "$SRC/mercury-2.2.0" "$SRC/build-mercury" && tar xjf "$SRC/mercury-2.2.0.tar.bz2" -C "$SRC"
  cmake -S "$SRC/mercury-2.2.0" -B "$SRC/build-mercury" -DCMAKE_C_COMPILER="$MPICC" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$TUO_DEPS" -DCMAKE_PREFIX_PATH="$TUO_DEPS" -DBUILD_SHARED_LIBS=ON \
    -DBUILD_TESTING=OFF -DMERCURY_USE_BOOST_PP=ON -DMERCURY_USE_CHECKSUMS=OFF -DNA_USE_OFI=ON -DNA_USE_SM=OFF
  cmake --build "$SRC/build-mercury" -j"$J" && cmake --install "$SRC/build-mercury"
}

build_pdc() {
  # Cray clang: PDC_TIMING globals are defined in a header, so link needs
  # -fcommon (PDC adds it for GCC only). TOSS 4's glibc keeps shm_open in librt,
  # which PDC does not link.
  pdc_build "$SRC/build-pdc" "$TUO_DEPS" -DCMAKE_C_COMPILER="$MPICC" \
    -DMERCURY_DIR="$TUO_DEPS/share/cmake/mercury" -DCMAKE_PREFIX_PATH="$TUO_DEPS" \
    -DCMAKE_C_FLAGS=-fcommon -DCMAKE_C_STANDARD_LIBRARIES=-lrt
}

build_bench() {
  local s=$AIO_ROOT/src api=
  grep -rqs PDCobj_create_coll "$TUO_DEPS/include" && api=-DPDC_COLL_API
  # BLAS for dgemm: cray-libsci through the cc wrapper; otherwise set BLAS_LIBS.
  "$MPICC" -O2 -march=native -Wall -I"$s" -I"$TUO_DEPS/include" -DHAVE_PDC $api \
    -o "$TUO_BIN/aio_bench_pdc" "$s"/aio_bench.c "$s"/trace.c "$s"/kernels.c "$s"/io_posix.c \
    "$s"/io_mpiio.c "$s"/io_pdc.c "$s"/gpu_stub.c "$s"/io_absent.c \
    -L"$TUO_DEPS/lib" -Wl,-rpath,"$TUO_DEPS/lib" -lpdc -lpdc_commons -lmercury ${BLAS_LIBS:-} -lrt -lpthread -lm
  "$MPICC" -O2 -march=native -Wall -I"$TUO_DEPS/include" -o "$TUO_BIN/progress_test" \
    "$s/progress_test/progress_test.c" \
    -L"$TUO_DEPS/lib" -Wl,-rpath,"$TUO_DEPS/lib" -lmercury -lna -lmercury_util -lpthread -lm
  echo "built $TUO_BIN/aio_bench_pdc ${api:+(collective API)} and $TUO_BIN/progress_test"
}

for c in ${*:-libfabric mercury pdc bench}; do
  echo "=== $c"
  "build_$c"
done
