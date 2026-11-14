# Helpers for the PDC job scripts; sourced by matrix.sh and smoke.sbatch.
PM_HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$PM_HERE/env.sh"

NODES=${SLURM_JOB_NUM_NODES:-1}
CPN=${CPN:-32}                 # PDC clients (aio_bench ranks) per node
SPN=${SPN:-2}                  # PDC servers per node
MB=${MB:-256}                  # MiB per client per step
STEPS=${STEPS:-5}
RATIO=${RATIO:-1.25}           # compute per step = RATIO x sync I/O-only time per step
BASE_REPS=${BASE_REPS:-2}      # sync I/O-only runs used to set the compute length
CASE_TIMEOUT=${CASE_TIMEOUT:-1200}
PM_NAME=${PM_NAME:-${SLURM_JOB_NAME:-pdc}}
CSV=${CSV:-$PM_RESULTS/${PM_NAME}_n${NODES}.csv}
LOG=${LOG:-$PM_RESULTS/${PM_NAME}_n${NODES}.log}
RUNS=$PM_RUNDIR/${SLURM_JOB_ID:-$$}
SEQ=0
mkdir -p "$PM_RESULTS" "$RUNS"

# Server cache: hold every step in memory (2 servers x 5 steps x 4 GiB per node
# at the defaults); PDCobj_flush_all_start at the end writes it to Lustre.
export PDC_SERVER_CACHE_MAX_SIZE=${PDC_SERVER_CACHE_MAX_SIZE:-96}
[ "${NOFLUSH:-0}" = 1 ] && export PDC_SERVER_CACHE_NO_FLUSH=1

SRV_MASK=$(python3 "$PM_HERE/cpumask.py" server "$SPN")

log() { echo "$(date '+%F %T') [$PM_NAME n$NODES] $*" | tee -a "$LOG" >&2; }

# set_transport cxi|tcp -> HG_TRANSPORT and the launch wrapper WRAP
set_transport() {
  case $1 in
    cxi) export HG_TRANSPORT=ofi+cxi; WRAP=() ;;
    tcp) export HG_TRANSPORT=ofi+tcp; WRAP=("$PM_HERE/hsn_host.sh") ;;
    *) log "unknown transport $1"; return 1 ;;
  esac
}

# Steps share the nodes, so every srun uses --overlap and binds by explicit
# masks; -c only sizes the step so that it may use the whole node.
start_servers() {
  local run=$1
  export PDC_TMPDIR=$run/tmp PDC_DATA_LOC=$run/data
  mkdir -p "$PDC_TMPDIR" "$PDC_DATA_LOC"
  srun --overlap -N "$NODES" --ntasks-per-node="$SPN" -c $((256 / SPN)) \
    --cpu-bind=mask_cpu:"$SRV_MASK" "${WRAP[@]}" stdbuf -oL -eL "$PDC_DIR/bin/pdc_server" > "$run/server.log" 2>&1 &
  SRV_PID=$!
  # Rank 0 writes server.cfg (atomically) once it has looked up every server;
  # clients read the same file. The "Server ready" log line is not a reliable
  # signal: PDC logs to stdout, which is block-buffered under srun.
  for _ in $(seq 360); do
    [ -s "$PDC_TMPDIR/server.cfg" ] && { sleep 1; return 0; }
    kill -0 "$SRV_PID" 2> /dev/null || break
    sleep 0.5
  done
  log "servers did not come up (exited: $(kill -0 "$SRV_PID" 2> /dev/null && echo no || echo yes)), see $run/server.log"
  stop_servers "$run"
  return 1
}

# Background jobs of a batch script start with SIGINT ignored, so stop an
# srun with SIGTERM (it cancels the step) and SIGKILL if that does not work.
kill_step() {
  kill -0 "$1" 2> /dev/null || return 0
  kill -TERM "$1"
  for _ in $(seq 40); do kill -0 "$1" 2> /dev/null || break; sleep 0.5; done
  kill -KILL "$1" 2> /dev/null
}

stop_servers() {
  local run=$1
  timeout 120 srun --overlap -N 1 -n 1 "${WRAP[@]}" "$PDC_DIR/bin/close_server" > "$run/close.log" 2>&1
  for _ in $(seq 120); do kill -0 "$SRV_PID" 2> /dev/null || break; sleep 0.5; done
  kill_step "$SRV_PID"
  wait "$SRV_PID" 2> /dev/null || true
}

# pdc_case MODE KERNEL TCOMP: one aio_bench run with fresh servers.
# Uses TRANSPORT (cxi|tcp), PLACEMENT (shared|smt|reserved), TARGET (local|remote).
# remote: every client writes the region owned by the servers on the next node,
# so all PDC traffic crosses Slingshot (AIO_PDC_SHIFT in src/io_pdc.c).
pdc_case() {
  local mode=$1 kernel=$2 tcomp=$3 run rc shift=0 cmask
  run=$RUNS/$((++SEQ))
  [ "$TARGET" = remote ] && shift=$CPN
  set_transport "$TRANSPORT" || return 1
  local tag=$TRANSPORT.$PLACEMENT.$TARGET
  mkdir -p "$run"
  start_servers "$run" || { log "FAIL $tag $mode $kernel (servers)"; return 1; }
  cmask=$(python3 "$PM_HERE/cpumask.py" client "$PLACEMENT" "$CPN")
  AIO_PDC_SHIFT=$shift timeout "$CASE_TIMEOUT" \
    srun --overlap -N "$NODES" --ntasks-per-node="$CPN" -c $((256 / CPN)) --cpu-bind=mask_cpu:"$cmask" \
    "${WRAP[@]}" "$PM_BIN/aio_bench_pdc" --io pdc --mode "$mode" --kernel "$kernel" --tcomp "$tcomp" \
    --mb "$MB" --steps "$STEPS" --dir "$run" --tag "$tag" --csv "$CSV" > "$run/client.log" 2>&1
  rc=$?
  stop_servers "$run"
  if [ $rc = 0 ]; then
    log "ok   ${REP:+rep $REP }$tag $mode $kernel: $(grep -a 'P=' "$run/client.log" | tail -1)"
    [ -n "${KEEP:-}" ] || rm -rf "$run"
  else
    log "FAIL ${REP:+rep $REP }$tag $mode $kernel rc=$rc, kept $run"
  fi
  return $rc
}

# io_time: median sync I/O-only time per step of the last BASE_REPS runs of
# the current TRANSPORT/PLACEMENT/TARGET at this node count.
io_time() {
  awk -F, -v tag="$TRANSPORT.$PLACEMENT.$TARGET" -v n=$((NODES * CPN)) \
    '$1 == "pdc" && $2 == "sync" && $3 == "none" && $4 == tag && $6 == n { print $14 }' "$CSV" |
    tail -n "$BASE_REPS" | sort -g | awk '{ v[NR] = $1 } END { if (NR) print (v[int((NR + 1) / 2)] + v[int(NR / 2) + 1]) / 2 }'
}
