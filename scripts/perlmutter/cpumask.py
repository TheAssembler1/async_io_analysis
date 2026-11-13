#!/usr/bin/env python3
"""Print a comma-separated srun --cpu-bind=mask_cpu list for one Perlmutter CPU node.

    cpumask.py server [n=2]                 n servers, each on one core and its SMT sibling
    cpumask.py client PLACEMENT [n=32]      n clients, PLACEMENT = shared | smt | reserved

A CPU node has 2 x 64-core EPYC 7763; CPUs 0-127 are the cores and CPU c+128 is
the SMT sibling of core c. Each socket has 4 NUMA domains of 16 cores.

Server j sits on core j*128/n (core 0 and 64 for two), the first core of a
NUMA domain. Client i sits on socket i//16, NUMA domain (i%16)//4, core
2+2*(i%4) of that domain, so local ranks 0-15 share socket 0 with server 0,
which owns their regions under PDC's static partition.
  shared    one hardware thread; PDC's progress thread shares it with the compute
  smt       the core's two hardware threads
  reserved  two cores (the one after it is free), no SMT sibling
"""
import sys


def mask(cpus):
    return hex(sum(1 << c for c in cpus))


def client_core(i):
    return (i // 16) * 64 + ((i % 16) // 4) * 16 + 2 + 2 * (i % 4)


def main(argv):
    if len(argv) >= 2 and argv[1] == "server":
        n = int(argv[2]) if len(argv) > 2 else 2
        assert n in (1, 2, 4, 8), "servers per node must be 1, 2, 4 or 8"
        cpus = [[j * 128 // n, j * 128 // n + 128] for j in range(n)]
    elif len(argv) >= 3 and argv[1] == "client":
        place, n = argv[2], int(argv[3]) if len(argv) > 3 else 32
        assert 1 <= n <= 32, "at most 32 clients per node"
        per = {"shared": lambda c: [c], "smt": lambda c: [c, c + 128], "reserved": lambda c: [c, c + 1]}
        if place not in per:
            sys.exit(f"unknown placement {place}")
        cpus = [per[place](client_core(i)) for i in range(n)]
    else:
        sys.exit(__doc__)
    print(",".join(mask(c) for c in cpus))


if __name__ == "__main__":
    main(sys.argv)
