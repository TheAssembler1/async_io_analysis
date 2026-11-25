#!/usr/bin/env bash
# Build libfabric, Mercury, OpenBLAS, PDC, aio_bench_pdc and progress_test into
# deps/install on the laptop (WSL, Open MPI).
#   build.sh [libfabric] [mercury] [openblas] [pdc] [bench]    default: all
# Versions follow PDC's docs (libfabric 1.18.0, Mercury 2.2.0). PDC comes from
# deps/src/pdc, cloned on the first build (branch: scripts/common/pdc.sh);
# after editing it, `build.sh pdc bench` rebuilds.
set -euo pipefail
source "$(dirname "$0")/env.sh"
source "$AIO_ROOT/scripts/common/pdc.sh"
J=${J:-$(nproc)}
SRC=$AIO_ROOT/deps/src
mkdir -p "$SRC" "$AIO_DEPS/bin"

fetch() { [ -e "$SRC/$2" ] || curl -fsSL -o "$SRC/$2" "$1"; }

build_libfabric() {
  fetch https://github.com/ofiwg/libfabric/releases/download/v1.18.0/libfabric-1.18.0.tar.bz2 libfabric-1.18.0.tar.bz2
  rm -rf "$SRC/libfabric-1.18.0" && tar xjf "$SRC/libfabric-1.18.0.tar.bz2" -C "$SRC"
  (cd "$SRC/libfabric-1.18.0" && ./configure -q --prefix="$AIO_DEPS" CC=gcc CFLAGS=-O2 \
    --enable-tcp=yes --enable-rxm=yes --enable-sockets=yes \
    --disable-verbs --disable-psm3 --disable-efa --disable-usnic --disable-opx &&
    make -s -j"$J" && make -s install)
}

build_mercury() {
  fetch https://github.com/mercury-hpc/mercury/releases/download/v2.2.0/mercury-2.2.0.tar.bz2 mercury-2.2.0.tar.bz2
  rm -rf "$SRC/mercury-2.2.0" "$SRC/build-mercury" && tar xjf "$SRC/mercury-2.2.0.tar.bz2" -C "$SRC"
  cmake -S "$SRC/mercury-2.2.0" -B "$SRC/build-mercury" -DCMAKE_C_COMPILER=gcc -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$AIO_DEPS" -DCMAKE_PREFIX_PATH="$AIO_DEPS" -DBUILD_SHARED_LIBS=ON \
    -DBUILD_TESTING=OFF -DMERCURY_USE_BOOST_PP=ON -DMERCURY_USE_CHECKSUMS=OFF -DNA_USE_OFI=ON -DNA_USE_SM=OFF
  cmake --build "$SRC/build-mercury" -j"$J" && cmake --install "$SRC/build-mercury"
}

build_openblas() {   # for the dgemm kernel
  fetch https://github.com/OpenMathLib/OpenBLAS/releases/download/v0.3.34/OpenBLAS-0.3.34.tar.gz OpenBLAS-0.3.34.tar.gz
  rm -rf "$SRC/OpenBLAS-0.3.34" && tar xzf "$SRC/OpenBLAS-0.3.34.tar.gz" -C "$SRC"
  make -s -C "$SRC/OpenBLAS-0.3.34" CC=gcc FC=false NOFORTRAN=1 USE_OPENMP=0 USE_THREAD=0 -j"$J"
  make -s -C "$SRC/OpenBLAS-0.3.34" PREFIX="$AIO_DEPS" NOFORTRAN=1 install
}

build_pdc() {
  pdc_build "$SRC/build-pdc" "$AIO_DEPS" -DCMAKE_C_COMPILER=mpicc \
    -DMERCURY_DIR="$AIO_DEPS/share/cmake/mercury" -DCMAKE_PREFIX_PATH="$AIO_DEPS"
}

build_bench() {
  local s=$AIO_ROOT/src api=
  grep -rqs PDCobj_create_coll "$AIO_DEPS/include" && api=-DPDC_COLL_API
  mpicc -O2 -march=native -Wall -I"$s" -I"$AIO_DEPS/include" -DHAVE_PDC $api \
    -o "$AIO_DEPS/bin/aio_bench_pdc" "$s"/aio_bench.c "$s"/trace.c "$s"/kernels.c "$s"/io_posix.c \
    "$s"/io_mpiio.c "$s"/io_pdc.c "$s"/gpu_stub.c "$s"/io_absent.c \
    -L"$AIO_DEPS/lib" -Wl,-rpath,"$AIO_DEPS/lib" -lpdc -lpdc_commons -lmercury -lopenblas -lrt -lpthread -lm
  make -s -C "$s/progress_test" MERCURY_DIR="$AIO_DEPS"
  echo "built $AIO_DEPS/bin/aio_bench_pdc ${api:+(collective API)} and $s/progress_test/progress_test"
}

for c in ${*:-libfabric mercury openblas pdc bench}; do
  echo "=== $c"
  "build_$c" > "$SRC/build_$c.log" 2>&1 || { echo "=== $c FAILED (see $SRC/build_$c.log)"; exit 1; }
done
