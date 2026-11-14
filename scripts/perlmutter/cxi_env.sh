#!/bin/bash
# Run "$@" for progress_test over ofi+cxi. Mercury 2.4 takes the CXI service and
# VNI from SLINGSHOT_SVC_IDS/SLINGSHOT_VNIS, which Slurm sets for every job step
# (with --network=job_vni the job VNI is in there, so separate steps can talk).
# Only when they are missing, fall back to the first enabled, non-system service
# open to this user, parsed from `cxi_service -v list` as Perlmutter prints it:
#   ID: 1 (DEFAULT) / Enabled : Yes / System Service : No /
#   ---> Valid Members : All uids/gids  (or a uid list) / VNIs : 1 10
if [[ ${PT_HG:-} == *cxi* && -z ${SLINGSHOT_SVC_IDS:-} ]]; then
  read -r svc vni < <(cxi_service -v list 2> /dev/null | awk -v uid="$(id -u)" '
    /^ *ID:/              { id = $2; en = sys = mem = 0 }
    /Enabled *:/          { en = /Yes/ }
    /System Service *:/   { sys = /Yes/ }
    /Valid Members *:/    { mem = /All uids/ || $0 ~ ("(^|[^0-9])" uid "([^0-9]|$)") }
    /^ *VNIs *:/          { if (en && !sys && mem) { print id, $3; exit } }')
  if [ -n "${svc:-}" ]; then
    export SLINGSHOT_SVC_IDS=$svc SLINGSHOT_VNIS=$vni SLINGSHOT_DEVICES=${HG_CXI_NIC:-cxi0}
  fi
fi
exec "$@"
