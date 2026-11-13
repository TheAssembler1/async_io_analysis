#!/bin/bash
# Run "$@" with HG_HOST set to this node's Slingshot (hsn0) address, so that
# ofi+tcp goes over the high-speed network rather than the management network.
# PDC reads HG_HOST; progress_test takes the host in PT_HG.
ip=$(ip -4 -o addr show hsn0 2> /dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)
if [ -n "$ip" ]; then
  export HG_HOST=$ip
  [ "${PT_HG:-}" = ofi+tcp ] && export PT_HG=ofi+tcp://$ip # progress_test
fi
exec "$@"
