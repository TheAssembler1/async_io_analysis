# PDC source checkout and build, shared by the per-machine build scripts.
# Sourced after AIO_ROOT is set.
#
# The source lives in deps/src/pdc (git-ignored). The first build clones
# PDC_REF from PDC_REPO: by default the in-flight transformations branch with
# the Perlmutter CXI endpoint fix on top. Later builds never fetch, reset or switch branches
# there, so edits and local branches survive: change the code, rerun the
# machine's build script (e.g. scripts/perlmutter/build.sh pdc bench) and it
# rebuilds incrementally. CLEAN=1 starts from an empty build directory.
# PDC_SRC points the build at another checkout.
PDC_REPO=${PDC_REPO:-https://github.com/TheAssembler1/pdc.git}
PDC_REF=${PDC_REF:-inflight-transformations-perlmutter-cxi}
PDC_SRC=${PDC_SRC:-$AIO_ROOT/deps/src/pdc}

pdc_checkout() {
  if [ ! -e "$PDC_SRC/.git" ]; then
    mkdir -p "$(dirname "$PDC_SRC")"
    git clone -q -b "$PDC_REF" "$PDC_REPO" "$PDC_SRC"
  fi
  local at
  at=$(git -C "$PDC_SRC" rev-parse --abbrev-ref HEAD)
  [ "$at" = "$PDC_REF" ] || echo "note: $PDC_SRC is on $at, not $PDC_REF; building it as is" >&2
  echo "PDC source $PDC_SRC ($at $(git -C "$PDC_SRC" describe --always --dirty))"
}

# pdc_build <build dir> <install prefix> [cmake args...]
pdc_build() {
  local b=$1 p=$2
  shift 2
  pdc_checkout
  [ "${CLEAN:-0}" = 1 ] && rm -rf "$b"
  # PDC_ENABLE_MULTITHREAD stays OFF (the default): busy-polling progress, as in the paper.
  cmake -S "$PDC_SRC" -B "$b" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$p" \
    -DBUILD_SHARED_LIBS=ON -DBUILD_TESTING=OFF -DBUILD_MPI_TESTING=OFF \
    -DPDC_ENABLE_MPI=ON -DPDC_SERVER_CACHE=ON "$@"
  cmake --build "$b" -j"${J:-8}"
  cmake --install "$b"
}
