#!/usr/bin/env bash
# Run one aio_bench configuration.
#   run_one.sh <binding> <nranks> <io> <mode> <kernel> [extra aio_bench args...]
# binding: full     one hardware thread per rank (background threads share it)
#          smt      rank owns its core's two hardware threads
#          reserved rank owns two physical cores (one for background threads)
#          none     no binding (OS scheduler free to migrate)
# Environment: AIO_MEMMAX (cgroup memory cap, default 24G), AIO_CSV,
#   AIO_HDF5_EXE_DCLOSE (async VOL trigger, default 1).
set -euo pipefail
source "$(dirname "$0")/env.sh"
bind=$1 np=$2 io=$3 mode=$4 kernel=$5; shift 5

case $bind in
  full)     list=$(seq -s, 0 $((np - 1))) ;;
  smt)      list=$(for ((r = 0; r < np; r++)); do printf "%d+%d," $r $((r + 48)); done) ;;
  # two physical cores on the same socket (even CPUs: socket 0, odd: socket 1)
  reserved) list=$(for ((r = 0; r < np; r++)); do s=$((r % 2)) i=$((r / 2)); printf "%d+%d," $((4 * i + s)) $((4 * i + 2 + s)); done) ;;
  none)     list= ;;
  *) echo "unknown binding $bind" >&2; exit 1 ;;
esac
bindopt=()
[ -n "$list" ] && bindopt=(-bind-to "user:${list%,}")

envs=()
if [ "$io" = hdf5 ] && [ "$mode" = async ]; then
  envs+=(-genv HDF5_VOL_CONNECTOR "async under_vol=0;under_info={}" -genv HDF5_PLUGIN_PATH "$HDF5_PLUGIN_PATH")
  # Start background execution at dataset close, as the vol-async docs
  # recommend for periodic checkpoint output (default: idle detection).
  envs+=(-genv HDF5_ASYNC_EXE_DCLOSE "${AIO_HDF5_EXE_DCLOSE:-1}")
fi
if [ "$io" = mpiio ] && [ "$mode" = async ]; then
  envs+=(-genv MPIR_CVAR_ASYNC_PROGRESS 1)
fi

# Collective HDF5 I/O by default (AIO_HDF5_INDEP=1 for independent).
envs+=(-genv AIO_HDF5_INDEP "${AIO_HDF5_INDEP:-0}")
dir=$AIO_SCRATCH/run_$$
rm -rf "$dir"; mkdir -p "$dir"
sync  # start every run with no dirty pages from earlier runs
systemd-run --user --scope --quiet -p MemoryMax=${AIO_MEMMAX:-24G} -p MemorySwapMax=0 \
  mpiexec -n "$np" "${bindopt[@]}" "${envs[@]}" \
  "$AIO_BENCH" --io "$io" --mode "$mode" --kernel "$kernel" --dir "$dir" --tag "$bind" \
  ${AIO_CSV:+--csv "$AIO_CSV"} "$@"
rc=$?
rm -rf "$dir"
exit $rc
