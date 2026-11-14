#!/bin/bash
# Run "$@" so that ofi+cxi processes in different job steps can reach each other.
#
# Slurm (switch/hpe_slingshot, job_vni) gives every multi-node step its own VNI
# and lists the job VNI second: SLINGSHOT_VNIS=<step vni>,<job vni>. Without an
# auth key, libfabric's cxi provider uses the first VNI, so the PDC servers
# (one step) and clients (another step) end up on different VNIs and every send
# fails with PTLTE_NOT_FOUND. Single-node steps get no step VNI on Perlmutter
# (single_node_vni=user), which is why single-node tests work. Putting the job
# VNI first makes it the default for everything we launch; the step's CXI
# service allows both.
#
# If SLINGSHOT_SVC_IDS is missing (not under Slurm), fall back to the first
# enabled, non-system service open to this user from `cxi_service -v list`.
if [[ ${HG_TRANSPORT:-}${PT_HG:-} == *cxi* ]]; then
  if [[ ${SLINGSHOT_VNIS:-} == *,* ]]; then
    IFS=, read -ra v <<< "$SLINGSHOT_VNIS"
    SLINGSHOT_VNIS=$(IFS=,; echo "${v[*]:1:1},${v[0]}${v[2]:+,}$(IFS=,; echo "${v[*]:2}")")
    export SLINGSHOT_VNIS
  fi
  if [ -z "${SLINGSHOT_SVC_IDS:-}" ]; then
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
  [ -n "${CXI_ENV_DEBUG:-}" ] && echo "cxi_env $(hostname) task ${SLURM_PROCID:-?}: VNIS=$SLINGSHOT_VNIS SVC_IDS=${SLINGSHOT_SVC_IDS:-}" >&2
fi
exec "$@"
