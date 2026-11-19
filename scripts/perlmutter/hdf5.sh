#!/bin/bash
# HDF5 (MPI-IO VFD on Lustre) with each kernel, blocking (native VOL) and
# asynchronous (async VOL), traced with DFTracer. Runs FLUSHES x PLACEMENTS x
# KERNELS x MODES, REPS times, repetition outermost. Called from
# jobs/hdf5_trace.sbatch; outside Slurm it uses mpirun, which is only for
# trying the scripts out.
#
# endflush: no per-step flush; after the last step the file is closed and every
# rank fsyncs it (--fsync-end), so the async VOL's write of step i can run
# during compute i+1 and the data is still on disk at the end.
# flush: every step ends with H5Fflush, as in the paper. The async VOL always
# runs a flush as a blocking task, so the main thread waits for the step's
# write there and nothing overlaps the next compute phase.
# noflush: no flush at all (--no-fsync).
#
# The compute length is fixed first per FLUSHES entry, as in matrix.sh:
# BASE_REPS sync runs with no compute give the I/O time per step, and every
# kernel then computes RATIO times that per step.
#
# The first TRACE_REPS repetitions of each case run under DFTracer (POSIX,
# MPI, MPI-IO and HDF5 calls of every thread) with aio_bench's own phase log
# and thread sampler (src/trace.c); later ones run untraced, for timings
# without the tracing overhead. Rows go to $PM_RESULTS/<name>_n<nodes>.csv with
# tag = <placement>.<flush mode>, plus .trace for traced runs; traces go to
# $TRACE_ROOT/<name>_n<nodes>/<placement>.<kernel>.<mode>.<flush mode>/.
#
# Placements (CPUs per rank from cpumask.py):
#   shared   one hardware thread: the async VOL thread shares it with the compute
#   reserved two cores, threads placed by the OS
#   iocore   two cores, main thread pinned to the first and every other thread
#            (the async VOL's execution stream) to the second (AIO_PIN=iocore)
set -u
PM_HERE=$(cd "$(dirname "$0")" && pwd)
source "$PM_HERE/hdf5_env.sh"

NODES=${SLURM_JOB_NUM_NODES:-${NODES:-1}}
CPN=${CPN:-32}
MB=${MB:-256}                  # MiB per rank per step
STEPS=${STEPS:-10}             # the last step's write and the final fsync never overlap compute
RATIO=${RATIO:-1.25}
COMPUTE_S=${COMPUTE_S:-}       # compute per step (s); unset: RATIO x the sync I/O baseline
ALONE_REPS=${ALONE_REPS:-3}    # standalone compute runs per case (aio_bench --alone-reps)
BASE_REPS=${BASE_REPS:-2}
REPS=${REPS:-3}
TRACE_REPS=${TRACE_REPS:-1}
SAMPLE_MS=${SAMPLE_MS:-5}      # thread sampler period in traced runs
STRIPE=${STRIPE:-16}           # Lustre stripe count of the output file (1 MiB stripes)
FLUSHES=${FLUSHES:-endflush}   # add flush for the paper's per-step H5Fflush (no overlap)
PLACEMENTS=${PLACEMENTS:-shared iocore}
KERNELS=${KERNELS:-sleep dgemm stream spin}   # also: halo net
MODES=${MODES:-sync async}
CASE_TIMEOUT=${CASE_TIMEOUT:-600}
HANG_RETRIES=${HANG_RETRIES:-1}   # reruns of a case killed by CASE_TIMEOUT (the async VOL hang)
# The async VOL copies each write's buffer and writes synchronously instead
# once its copies would pass this many MiB per process. Unset, it takes the
# free RAM at startup (page cache counts as used), which on a node full of
# cached file data can be less than one step's buffer.
ASYNC_MAX_MEM_MB=${ASYNC_MAX_MEM_MB:-$((${MB%.*} + 256))}   # one step in flight
# 1: H5VL_async_start() at init, so tasks run as soon as queued (AIO_HDF5_START
# in src/io_hdf5.c). With 0 the async VOL holds a step's write until the next
# blocking call, which removes most of the overlap, and its collective tasks
# hang now and then (one rank's never starts). Runs with 0 get tag .nostart.
ASYNC_START=${ASYNC_START:-1}
NAME=${PM_NAME:-${SLURM_JOB_NAME:-hdf5_trace}}
CSV=${CSV:-$PM_RESULTS/${NAME}_n${NODES}.csv}
LOG=${LOG:-$PM_RESULTS/${NAME}_n${NODES}.log}
TRACE_ROOT=${TRACE_ROOT:-$PM_RESULTS/hdf5_traces}   # git-ignored; a few GB per Perlmutter job
TRACES=$TRACE_ROOT/${NAME}_n${NODES}
RUNS=$PM_RUNDIR/${SLURM_JOB_ID:-$$}
SEQ=0
mkdir -p "$PM_RESULTS" "$TRACES" "$RUNS"

log() { echo "$(date '+%F %T') [$NAME n$NODES] $*" | tee -a "$LOG" >&2; }

[ -x "$PM_H5_BENCH" ] || { log "$PM_H5_BENCH missing, run build_hdf5.sh"; exit 1; }
[ -n "$DFT_PRELOAD" ] || { log "libdftracer_preload.so not in $PM_H5_DIR, run build_hdf5.sh"; exit 1; }
[ -f "$AIO_MONOCLOCK" ] || { log "$AIO_MONOCLOCK missing, run build_hdf5.sh bench"; exit 1; }

# launch PLACEMENT cmd...: one rank per client slot of cpumask.py.
launch() {
  local place=$1 cmask pe=1
  shift
  if [ -n "${SLURM_JOB_ID:-}" ]; then
    cmask=$(python3 "$PM_HERE/cpumask.py" client "${place/iocore/reserved}" "$CPN")
    timeout "$CASE_TIMEOUT" srun -N "$NODES" --ntasks-per-node="$CPN" -c $((256 / CPN)) \
      --cpu-bind=mask_cpu:"$cmask" "$@"
  else
    [ "$place" = shared ] || pe=2
    # Start each case with no dirty pages left over from the last one, which
    # otherwise get written back during this case's I/O.
    sync
    timeout "$CASE_TIMEOUT" mpirun -np "$CPN" --map-by "slot:PE=$pe" --bind-to hwthread "$@"
  fi
}

# h5_case MODE KERNEL TCOMP TRACED
h5_case() {
  local mode=$1 kernel=$2 tcomp=$3 traced=$4 run rc tag=$PLACEMENT.$FLUSH tdir= envs=() fopt=
  case $FLUSH in
    flush) ;;
    endflush) fopt=--fsync-end ;;
    noflush) fopt=--no-fsync ;;
    *) log "unknown flush mode $FLUSH"; return 1 ;;
  esac
  run=$RUNS/$((++SEQ))
  mkdir -p "$run"
  command -v lfs > /dev/null && lfs setstripe -c "$STRIPE" -S 1m "$run" 2> /dev/null
  [ "$mode" = async ] && envs+=("HDF5_VOL_CONNECTOR=async under_vol=0;under_info={}" HDF5_ASYNC_MAX_MEM_MB="$ASYNC_MAX_MEM_MB")
  [ "$PLACEMENT" = iocore ] && envs+=(AIO_PIN=iocore)
  if [ "$mode" = async ]; then
    if [ "$ASYNC_START" = 1 ]; then envs+=(AIO_HDF5_START=1); else tag=$tag.nostart; fi
  fi
  if [ "$traced" = 1 ]; then
    tag=$tag.trace
    tdir=$TRACES/$PLACEMENT.$kernel.$mode.$FLUSH
    rm -rf "$tdir" && mkdir -p "$tdir"
    # File calls are traced only under the run directory, which leaves out
    # the sampler's /proc reads; MPI and HDF5 calls are traced regardless.
    # Events shorter than 100 us are folded into per-call counts (DFTracer's
    # default selective aggregation), which keeps the halo kernel's MPI calls
    # from dominating the trace.
    # The monoclock shim keeps DFTracer's and aio_bench's timestamps on one
    # clock that does not step (src/monoclock.c).
    envs+=(LD_PRELOAD="$AIO_MONOCLOCK:$DFT_PRELOAD" DFTRACER_ENABLE=1 DFTRACER_INIT=PRELOAD
      DFTRACER_LOG_FILE="$tdir/dft" DFTRACER_DATA_DIR="$run" DFTRACER_INC_METADATA=1
      DFTRACER_TRACE_COMPRESSION=1 AIO_TRACE="$tdir/aio" AIO_SAMPLE_MS="$SAMPLE_MS")
  fi
  launch "$PLACEMENT" env -u HDF5_VOL_CONNECTOR -u AIO_PIN -u AIO_TRACE -u AIO_HDF5_START -u HDF5_ASYNC_MAX_MEM_MB "${envs[@]}" \
    "$PM_H5_BENCH" --io hdf5 --mode "$mode" --kernel "$kernel" --tcomp "$tcomp" \
    --mb "$MB" --steps "$STEPS" --dir "$run" --tag "$tag" --csv "$CSV" --alone-reps "$ALONE_REPS" $fopt \
    > "$run/out.log" 2>&1
  rc=$?
  [ -n "$tdir" ] && cp "$run/out.log" "$tdir/out.log"
  if [ $rc = 0 ] && [ "$mode" = async ] && grep -q "async VOL not active" "$run/out.log"; then
    log "async VOL did not load, see $run/out.log"
    rc=1
  fi
  if [ $rc = 0 ]; then
    log "ok   ${REP:+rep $REP }$tag $mode $kernel: $(grep -a 'P=' "$run/out.log" | tail -1)"
    rm -rf "$run"
  elif [ $rc = 124 ]; then
    # The async VOL hang: one rank's collective task never starts while the
    # others wait in it. Seen in a few percent of async runs on the WSL laptop
    # with ASYNC_START=0, none in 14 with ASYNC_START=1.
    log "HANG ${REP:+rep $REP }$tag $mode $kernel (killed after $CASE_TIMEOUT s), kept $run"
  else
    log "FAIL ${REP:+rep $REP }$tag $mode $kernel rc=$rc, kept $run"
  fi
  return $rc
}

# Median sync I/O-only time per step of the untraced shared runs with this FLUSH.
io_time() {
  awk -F, -v n=$((NODES * CPN)) -v tag="shared.$FLUSH" \
    '$1 == "hdf5" && $2 == "sync" && $3 == "none" && $4 == tag && $6 == n { print $14 }' "$CSV" |
    tail -n "$BASE_REPS" | sort -g | awk '{ v[NR] = $1 } END { if (NR) print (v[int((NR + 1) / 2)] + v[int(NR / 2) + 1]) / 2 }'
}

log "start: flushes=[$FLUSHES] placements=[$PLACEMENTS] kernels=[$KERNELS] modes=[$MODES] reps=$REPS (traced $TRACE_REPS)," \
  "$CPN ranks per node, $MB MiB x $STEPS steps, stripe $STRIPE, traces in $TRACES"

declare -A TCOMP
PLACEMENT=shared
for FLUSH in $FLUSHES; do
  if [ -n "$COMPUTE_S" ]; then
    TCOMP[$FLUSH]=$COMPUTE_S
    log "$FLUSH: compute $COMPUTE_S s/step (COMPUTE_S)"
    continue
  fi
  for _ in $(seq "$BASE_REPS"); do h5_case sync none 0 0; done
  t=$(io_time)
  if [ -z "$t" ]; then log "no I/O baseline for $FLUSH, skipping it"; continue; fi
  TCOMP[$FLUSH]=$(awk -v t="$t" -v r="$RATIO" 'BEGIN { printf "%.3f", t * r }')
  log "$FLUSH: sync I/O $t s/step -> compute ${TCOMP[$FLUSH]} s/step"
done

for rep in $(seq "$REPS"); do
  traced=$([ "$rep" -le "$TRACE_REPS" ] && echo 1 || echo 0)
  for FLUSH in $FLUSHES; do
    [ -n "${TCOMP[$FLUSH]:-}" ] || continue
    for PLACEMENT in $PLACEMENTS; do
      for k in $KERNELS; do
        for m in $MODES; do
          for try in $(seq 0 "$HANG_RETRIES"); do
            REP=$rep/$REPS h5_case "$m" "$k" "${TCOMP[$FLUSH]}" "$traced"
            [ $? = 124 ] || break
            [ "$try" -lt "$HANG_RETRIES" ] && log "retrying after the hang"
          done
        done
      done
    done
  done
done
log "done"
