#!/usr/bin/env bash
# Run one aio_bench configuration against PDC.
#   run_pdc.sh <nservers> <nclients> <mode> <kernel> [extra aio_bench args...]
# PDC servers run on their own cores (the PDC deployment model: servers on
# cores reserved for them); clients are pinned one per remaining core.
# Default placement: servers on cores 0..S-1 (with their SMT siblings),
# clients on cores S..S+C-1.  Override with AIO_PDC_SERVER_CPUS and
# AIO_PDC_CLIENT_CPUS (comma-separated hydra binding lists).
# Environment: AIO_PDC_ENV (PDC install env script), AIO_MEMMAX, AIO_CSV,
#   AIO_PDC_TAG (CSV tag, default pdc<nservers>),
#   AIO_PDC_KEEP=1 keeps the output directory for inspection.
set -euo pipefail
source "$(dirname "$0")/env.sh"
source "${AIO_PDC_ENV:-/mnt/fast/nlewis/workspace/pdc_env.sh}"
export LD_LIBRARY_PATH="$PDC_DIR/lib:$LD_LIBRARY_PATH"
ns=$1 nc=$2 mode=$3 kernel=$4; shift 4

srv_cpus=${AIO_PDC_SERVER_CPUS:-$(for ((i = 0; i < ns; i++)); do printf "%d+%d," $i $((i + 48)); done)}
cli_cpus=${AIO_PDC_CLIENT_CPUS:-$(seq -s, $ns $((ns + nc - 1)))}

dir=$AIO_SCRATCH/pdc_$$
rm -rf "$dir"; mkdir -p "$dir/data" "$dir/tmp"
export PDC_TMPDIR=$dir/tmp PDC_DATA_LOC=$dir/data
sync  # start every run with no dirty pages from earlier runs
cd "$dir"

cleanup() {
  mpiexec -n 1 "$PDC_DIR/bin/close_server" > close.log 2>&1 || true
  wait 2> /dev/null || true
  cd /; [ -n "${AIO_PDC_KEEP:-}" ] || rm -rf "$dir"
}
trap cleanup EXIT

# Servers in the background; clients wait until every server has published
# its address in PDC_TMPDIR.
# Same storage budget as the other backends: the server's region cache is
# capped at 24 GiB and the server runs in its own cgroup with 24 GiB of page
# cache on top of that.
export PDC_SERVER_CACHE_MAX_SIZE=${PDC_SERVER_CACHE_MAX_SIZE:-24}
systemd-run --user --scope --quiet -p MemoryMax=${AIO_PDC_SERVER_MEMMAX:-56G} -p MemorySwapMax=0 \
  mpiexec -n "$ns" -bind-to "user:${srv_cpus%,}" "$PDC_DIR/bin/pdc_server" > server.log 2>&1 &
for _ in $(seq 120); do
  [ -s "$PDC_TMPDIR/server.conf" ] || [ -n "$(ls "$PDC_TMPDIR" 2> /dev/null)" ] && grep -q -i 'ready\|listening\|Server Ready' server.log 2> /dev/null && break
  sleep 0.5
done
sleep 2

systemd-run --user --scope --quiet -p MemoryMax=${AIO_MEMMAX:-24G} -p MemorySwapMax=0 \
  mpiexec -n "$nc" -bind-to "user:${cli_cpus%,}" \
  "$AIO_ROOT/src/aio_bench_pdc" --io pdc --mode "$mode" --kernel "$kernel" --dir "$dir" \
  --tag "${AIO_PDC_TAG:-pdc$ns}" ${AIO_CSV:+--csv "$AIO_CSV"} "$@"
