# PDC runs on Perlmutter

Job scripts for running the PDC experiments on Perlmutter CPU nodes over
Slingshot. Each node runs 32 PDC clients (`aio_bench_pdc` ranks) and 2 PDC
servers, from 1 to 8 nodes. Every run starts its own set of servers.

    source env.sh
    ./build.sh                          # Mercury, PDC, aio_bench_pdc, progress_test
    sbatch jobs/smoke.sbatch            # check the output before the rest
    ./submit_all.sh
    ./summary.py > ../../results/perlmutter/summary.md

## Node layout

Each NUMA domain gets 4 clients on its cores 2, 4, 6 and 8 (`cpumask.py`
masks). The server step asks for one core per server and Slurm's
`block:cyclic` puts them on core 0 and core 16 (the first cores of NUMA
domains 0 and 1, with their SMT siblings), which no client uses. So both
servers are on socket 0, and clients 16-31 reach theirs across sockets.

Steps sharing a node must not ask for much more than one node's CPUs in
total: the Slingshot plugin sizes each step's NIC reservation by its CPUs, and
a second step fails with "Error configuring interconnect" otherwise (see
`netcheck.sh`). CPU masks also have to lie inside the step's own allocation,
which is why the server step is placed rather than masked. Client placements:

| placement | client CPUs | PDC progress thread |
|---|---|---|
| shared | one hardware thread | shares it with the compute (PDC as deployed) |
| smt | the core's two hardware threads | can run on the sibling |
| reserved | two cores | can run on the second core |

## Network-bound clients

The scripts make clients network-bound in two ways:

- **`TARGET=remote`** (the default): rank r writes the region of rank r+32
  (`AIO_PDC_SHIFT` in `src/io_pdc.c`). Every client's data then goes to the
  servers on the next node, so every byte crosses Slingshot. With
  `TARGET=local`, clients write to their own node's servers. With one node,
  remote and local are the same, so remote is dropped.
- **Network kernels**: the compute phase itself uses the NIC. `net`
  (new in `src/kernels.c`) has every rank exchange 8 MiB with the same local
  rank on the next and previous node, which saturates the NIC in both
  directions. `halo` is the 3-D halo exchange from the paper, and only part
  of its traffic leaves the node. On one node, both kernels stay in shared
  memory.

## Jobs

| job | nodes | what varies |
|---|---|---|
| `smoke` | 2, debug QoS | every path once with 32 MiB transfers, plus network setup output |
| `scale` | 1, 2, 4, 8 | kernels sleep, spin, dgemm, stream, halo, net; sync and async |
| `placement` | 2 | shared / smt / reserved |
| `transport` | 2 | ofi+cxi against ofi+tcp over hsn0 (the paper's transport); not submitted by default |
| `locality` | 2, 4, 8 | local vs. remote servers |
| `knobs` | 2 | `progress_test` (Mercury only): HG_Progress timeout x sleep grid, blocking, placement; server on one node, 32 clients on the other |

The PDC jobs share one driver, `matrix.sh`. It first runs `BASE_REPS` sync,
I/O-only runs for each transport and target. Compute per step is then set to
`RATIO` (1.25) times that I/O time, as in the paper. After that it loops
over the lists, repetition outermost. Every list and size can be overridden
from the environment at submit time, e.g.
`KERNELS="spin net" REPS=5 sbatch -N 4 jobs/scale.sbatch`. The main knobs are
`MB` (256 MiB per client per step), `STEPS` (5), `REPS` (3), `CPN` (32),
`SPN` (2) and `NOFLUSH=1` (skip the final flush to Lustre).

Results go to `results/perlmutter/<job>_n<nodes>.csv` with the usual
`aio_bench` columns. The tag is `transport.placement.target`, and the node
count is `nranks / 32`. Each case is logged to `<job>_n<nodes>.log`. The
`knobs` job writes `knobs.csv` with the laptop sweep's columns plus `location`.
Run directories under `$PSCRATCH/aio_runs/<jobid>` are kept only for failed
cases.

## Things the smoke job checks

- **Job VNI**: the jobs request `--network=job_vni`, so the server step
  and the client step can reach each other over CXI. The smoke output prints
  `SLINGSHOT_VNIS` per step and `cxi_service -v list`.
- **PDC branch**: the default is `fix/perlmutter-cxi-defaults` from
  hpc-io/pdc. That branch gives each process a `cxi0:<pid>` endpoint and picks
  the CXI service the user belongs to. Stock PDC builds
  `ofi+cxi://<hostname>:<port>`, which CXI cannot use. Override with `PDC_REF`
  and `PDC_REPO`. `build.sh` detects whether PDC has the renamed collective
  calls (`PDCobj_create_coll`) and builds `io_pdc.c` to match.
- **ofi+tcp**: `hsn_host.sh` sets `HG_HOST` to the node's hsn0 address,
  so tcp runs over the same NIC as cxi and does not use the management network.

Versions: Mercury 2.4.1, not the 2.2.0 that PDC's install docs and the paper
use. 2.2.0 cannot open a cxi endpoint with the system libfabric (2.3.1 as of
this writing): it sets `rx_ctx_bits` on its address vector, which the cxi
provider rejects. The docs' libfabric 1.18.0 has no cxi provider at all.
`build.sh` adds the `mercury_list.h` header PDC still includes. Also Cray MPICH
with `MPICH_MAX_THREAD_SAFETY=multiple`, and PDC data on Lustre (`$PSCRATCH`).
