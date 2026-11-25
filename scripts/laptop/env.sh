# Source on the laptop (WSL) before running the PDC stack built by build.sh.
# PDC and Mercury talk ofi+tcp over loopback.
AIO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
export AIO_ROOT
export AIO_DEPS=${AIO_DEPS:-$AIO_ROOT/deps/install}
export LIBFABRIC_DIR=$AIO_DEPS MERCURY_DIR=$AIO_DEPS PDC_DIR=$AIO_DEPS
export PATH=$AIO_DEPS/bin:$PATH
export LD_LIBRARY_PATH=$AIO_DEPS/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
export PKG_CONFIG_PATH=$AIO_DEPS/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}
export HG_TRANSPORT=${HG_TRANSPORT:-ofi+tcp}
export HG_HOST=${HG_HOST:-127.0.0.1}
export OMPI_MCA_rmaps_base_oversubscribe=1
export PDC_TMPDIR=${PDC_TMPDIR:-$AIO_ROOT/deps/run/pdc_tmp}
export PDC_DATA_LOC=${PDC_DATA_LOC:-$AIO_ROOT/deps/run/pdc_data}
mkdir -p "$PDC_TMPDIR" "$PDC_DATA_LOC"
