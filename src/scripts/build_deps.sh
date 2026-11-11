#!/usr/bin/env bash
# Builds the I/O software stack used by the benchmarks into deps/install.
# Usage: src/scripts/build_deps.sh [component...]
#   components: argobots hdf5 volasync adios2 openblas mercury pdc tectonic (default: all)
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
SRC=$ROOT/deps/src
PREFIX=$ROOT/deps/install
J=${J:-48}
export CC=mpicc CXX=mpicxx
mkdir -p "$SRC" "$PREFIX/bin"
cd "$SRC"

fetch() { [ -e "$2" ] || curl -sL -o "$2" "$1"; }

build_argobots() {
  fetch https://github.com/pmodels/argobots/releases/download/v1.2/argobots-1.2.tar.gz argobots-1.2.tar.gz
  rm -rf argobots-1.2 && tar xf argobots-1.2.tar.gz && cd argobots-1.2
  CC=gcc ./configure --prefix="$PREFIX" && make -j$J && make install
}

build_hdf5() {   # parallel + thread-safe, required by the async VOL
  fetch https://github.com/HDFGroup/hdf5/releases/download/hdf5_1.14.6/hdf5-1.14.6.tar.gz hdf5-1.14.6.tar.gz
  rm -rf hdf5-1.14.6 hdf5-build && tar xf hdf5-1.14.6.tar.gz
  cmake -S hdf5-1.14.6 -B hdf5-build -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_BUILD_TYPE=Release \
    -DHDF5_ENABLE_PARALLEL=ON -DHDF5_ENABLE_THREADSAFE=ON -DALLOW_UNSUPPORTED=ON \
    -DHDF5_BUILD_CPP_LIB=OFF -DHDF5_BUILD_FORTRAN=OFF -DHDF5_BUILD_JAVA=OFF \
    -DBUILD_TESTING=OFF -DHDF5_BUILD_EXAMPLES=OFF -DHDF5_ENABLE_Z_LIB_SUPPORT=OFF -DHDF5_ENABLE_SZIP_SUPPORT=OFF
  cmake --build hdf5-build -j$J && cmake --install hdf5-build
}

build_volasync() {
  [ -d vol-async ] || git clone -q https://github.com/hpc-io/vol-async.git
  # 1 s / 100 ms back-offs in H5VL_async_request_wait -> 1 ms (see paper)
  git -C vol-async apply --check "$ROOT/src/scripts/vol-async-wait.patch" 2>/dev/null &&
    git -C vol-async apply "$ROOT/src/scripts/vol-async-wait.patch"
  rm -rf volasync-build
  cmake -S vol-async -B volasync-build -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH="$PREFIX" -DHDF5_DIR="$PREFIX/cmake" -DABT_DIR="$PREFIX" -DENABLE_WRITE_MEMCPY=ON
  cmake --build volasync-build -j$J && cmake --install volasync-build
}

build_adios2() {
  fetch https://github.com/ornladios/ADIOS2/archive/refs/tags/v2.12.1.tar.gz adios2-2.12.1.tar.gz
  rm -rf ADIOS2-2.12.1 adios2-build && tar xf adios2-2.12.1.tar.gz
  # opt-in per-step fdatasync (ADIOS2_FSYNC=1), see adios2-fsync.patch
  (cd ADIOS2-2.12.1/.. && patch -p1 -d ADIOS2-2.12.1 < "$ROOT/src/scripts/adios2-fsync.patch")
  cmake -S ADIOS2-2.12.1 -B adios2-build -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_BUILD_TYPE=Release \
    -DADIOS2_USE_MPI=ON -DADIOS2_USE_Fortran=OFF -DADIOS2_USE_Python=OFF -DADIOS2_USE_HDF5=OFF \
    -DADIOS2_USE_SST=OFF -DADIOS2_USE_CUDA=OFF -DADIOS2_USE_Kokkos=OFF -DADIOS2_USE_Campaign=OFF \
    -DADIOS2_USE_ZeroMQ=OFF -DADIOS2_USE_Blosc2=OFF -DADIOS2_USE_BZip2=OFF -DADIOS2_USE_ZFP=OFF \
    -DADIOS2_USE_SZ=OFF -DADIOS2_USE_MGARD=OFF -DADIOS2_USE_PNG=OFF -DADIOS2_USE_AWSSDK=OFF \
    -DADIOS2_USE_DataSpaces=OFF -DADIOS2_USE_DAOS=OFF -DADIOS2_USE_UCX=OFF \
    -DBUILD_TESTING=OFF -DADIOS2_BUILD_EXAMPLES=OFF
  cmake --build adios2-build -j$J && cmake --install adios2-build
}

build_openblas() {
  fetch https://github.com/OpenMathLib/OpenBLAS/releases/download/v0.3.34/OpenBLAS-0.3.34.tar.gz OpenBLAS-0.3.34.tar.gz
  rm -rf OpenBLAS-0.3.34 && tar xf OpenBLAS-0.3.34.tar.gz && cd OpenBLAS-0.3.34
  make CC=gcc FC=false NOFORTRAN=1 USE_OPENMP=0 USE_THREAD=0 TARGET=SAPPHIRERAPIDS -j$J
  make PREFIX="$PREFIX" NOFORTRAN=1 install
}

build_mercury() {  # shared-memory NA plugin only (single node)
  fetch https://github.com/mercury-hpc/mercury/releases/download/v2.3.1/mercury-2.3.1.tar.bz2 mercury-2.3.1.tar.bz2
  rm -rf mercury-2.3.1 mercury-build && tar xf mercury-2.3.1.tar.bz2
  CC=gcc CXX=g++ cmake -S mercury-2.3.1 -B mercury-build -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=ON -DNA_USE_SM=ON -DNA_USE_OFI=OFF -DNA_USE_UCX=OFF -DMERCURY_USE_BOOST_PP=ON \
    -DBUILD_TESTING=OFF -DMERCURY_USE_CHECKSUMS=OFF
  cmake --build mercury-build -j$J && cmake --install mercury-build
}

build_pdc() {
  [ -d pdc ] || git clone -q https://github.com/hpc-io/pdc.git
  rm -rf pdc-build
  cmake -S pdc -B pdc-build -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH="$PREFIX" -DMERCURY_DIR="$PREFIX/share/cmake/mercury" \
    -DPDC_ENABLE_MPI=ON -DBUILD_TESTING=OFF -DPDC_ENABLE_FASTBIT=OFF -DBUILD_MPI_TESTING=OFF \
    -DPDC_SERVER_CACHE=ON -DCMAKE_C_FLAGS="-Wno-error" -DCMAKE_POLICY_VERSION_MINIMUM=3.5
  cmake --build pdc-build -j$J && cmake --install pdc-build
}

build_tectonic() {
  fetch "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%400.17.0/tectonic-0.17.0-x86_64-unknown-linux-musl.tar.gz" tectonic.tar.gz
  tar xf tectonic.tar.gz -C "$PREFIX/bin"
}

COMPONENTS=${*:-argobots hdf5 volasync adios2 openblas mercury pdc tectonic}
for c in $COMPONENTS; do
  echo "=== building $c"; (cd "$SRC" && build_$c) > "$SRC/build_$c.log" 2>&1 && echo "=== $c OK" || { echo "=== $c FAILED (see $SRC/build_$c.log)"; exit 1; }
done
