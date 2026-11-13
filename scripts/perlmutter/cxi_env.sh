#!/bin/bash
# Run "$@" with the CXI service the PDC CXI branch would pick: the first
# enabled, non-system service that lists this user as a member. Only used for
# progress_test; PDC does the same lookup itself. Pass through if PT_HG is not
# ofi+cxi or if SLINGSHOT_SVC_IDS is already forced through PT_KEEP_SLINGSHOT=1.
if [[ ${PT_HG:-} == *cxi* && -z ${PT_KEEP_SLINGSHOT:-} ]]; then
  read -r svc vni < <(cxi_service -v list 2> /dev/null | awk -v uid="uid=$(id -u)" '
    /^ *ID:/            { id = $2; en = sys = mem = 0 }
    /Enabled/           { en = /Yes/ }
    /System Service/    { sys = /Yes/ }
    /Valid Members/     { mem = index($0, uid) > 0 }
    /---> Valid VNIs/   { if (en && !sys && mem) { print id, $NF; exit } }')
  if [ -n "${svc:-}" ]; then
    export SLINGSHOT_SVC_IDS=$svc SLINGSHOT_VNIS=$vni SLINGSHOT_DEVICES=${HG_CXI_NIC:-cxi0}
  fi
fi
exec "$@"
