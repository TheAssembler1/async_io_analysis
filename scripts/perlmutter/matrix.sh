#!/bin/bash
# Run TRANSPORTS x TARGETS x PLACEMENTS x KERNELS x MODES with PDC, REPS times
# (repetition outermost, so a job cut short still has every configuration once).
# Called from the sbatch scripts, which set the lists; everything has a default.
#
# Per TRANSPORT/TARGET the compute length is fixed first: BASE_REPS sync runs
# with no compute (kernel none, shared placement) give the I/O time per step,
# and every kernel then computes RATIO times that per step, as in the paper.
# Rows go to $PM_RESULTS/<job name>_n<nodes>.csv; tag = transport.placement.target.
set -u
source "$(dirname "$0")/common.sh"

TRANSPORTS=${TRANSPORTS:-cxi}
TARGETS=${TARGETS:-remote}
PLACEMENTS=${PLACEMENTS:-shared}
KERNELS=${KERNELS:-sleep spin dgemm stream halo net}
MODES=${MODES:-sync async}
REPS=${REPS:-3}

# One node has no next node: remote would equal local.
[ "$NODES" = 1 ] && TARGETS=$(tr ' ' '\n' <<< "$TARGETS" | sed 's/remote/local/' | sort -u | xargs)

log "start: transports=[$TRANSPORTS] targets=[$TARGETS] placements=[$PLACEMENTS] kernels=[$KERNELS]" \
  "modes=[$MODES] reps=$REPS, $CPN clients + $SPN servers per node, $MB MiB x $STEPS steps," \
  "nodes $(scontrol show hostnames "$SLURM_JOB_NODELIST" | xargs)"

declare -A TCOMP
for TRANSPORT in $TRANSPORTS; do
  for TARGET in $TARGETS; do
    PLACEMENT=shared
    for _ in $(seq "$BASE_REPS"); do pdc_case sync none 0; done
    t=$(io_time)
    if [ -z "$t" ]; then log "no I/O baseline for $TRANSPORT/$TARGET, skipping it"; continue; fi
    TCOMP[$TRANSPORT.$TARGET]=$(awk -v t="$t" -v r="$RATIO" 'BEGIN { printf "%.3f", t * r }')
    log "$TRANSPORT/$TARGET: sync I/O $t s/step -> compute ${TCOMP[$TRANSPORT.$TARGET]} s/step"
  done
done

for rep in $(seq "$REPS"); do
  for TRANSPORT in $TRANSPORTS; do
    for TARGET in $TARGETS; do
      tc=${TCOMP[$TRANSPORT.$TARGET]:-}
      [ -n "$tc" ] || continue
      for PLACEMENT in $PLACEMENTS; do
        for k in $KERNELS; do
          for m in $MODES; do
            REP=$rep/$REPS pdc_case "$m" "$k" "$tc"
          done
        done
      done
    done
  done
done
log "done"
