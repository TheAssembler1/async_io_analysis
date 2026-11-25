# PDC runs on Perlmutter

Job scripts for running the PDC experiments on Perlmutter CPU nodes over
Slingshot. Each node runs 32 PDC clients (`aio_bench_pdc` ranks) and 2 PDC
servers, from 1 to 8 nodes. Every run starts its own set of servers.

    source env.sh
    ./build.sh                          # Mercury, PDC, aio_bench_pdc, progress_test
    sbatch jobs/smoke.sbatch            # check the output before the rest
    ./submit_all.sh
    ../../analysis/perlmutter/summary.py > ../../results/perlmutter/summary.md
    ../../analysis/perlmutter/plots.py  # PNGs in figures/perlmutter (needs matplotlib)

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
| `knobs` | 2 | `progress_test` (Mercury only): HG_Progress timeout x sleep grid, blocking, placement; server on one node, 32 clients on the other (`GRID_PLACEMENTS="smt reserved" BASE=0` runs the grid for the other placements) |

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

# Traced HDF5 runs

`hdf5.sh` (job `jobs/hdf5_trace.sbatch`, 2 nodes) runs `aio_bench` with the
HDF5 backend on Lustre, sync (native VOL) and async (async VOL), and traces
the first repetition of each case. No PDC here.

    source hdf5_env.sh
    ./build_hdf5.sh                     # Argobots, HDF5, vol-async, DFTracer, aio_bench_h5
    sbatch -q debug -t 00:30:00 --export=ALL,KERNELS=dgemm,REPS=1,MB=16,STEPS=3 jobs/hdf5_trace.sbatch
    sbatch jobs/hdf5_trace.sbatch
    ../../analysis/perlmutter/hdf5_summary.py      # timings of the untraced runs
    ../../analysis/perlmutter/hdf5_traces.py ../../results/perlmutter/hdf5_traces/hdf5_trace_n2

`build_hdf5.sh` builds HDF5 1.14.6 parallel and thread-safe (the async VOL
needs both; cray-hdf5-parallel is not thread-safe), Argobots 1.2, vol-async
with the paper's 1 ms wait patch, and DFTracer with MPI and HDF5 interception
against that HDF5 and Cray MPICH. `hdf5_env.sh` unloads darshan, which would
intercept the same calls.

Each job runs `FLUSHES x PLACEMENTS x KERNELS x MODES`, `REPS` (3) times, `STEPS`
(10) steps of `MB` (256) MiB per rank, with the compute length per step set
to `RATIO` (1.25) times the sync I/O time per step, as in `matrix.sh`
(`COMPUTE_S` sets it directly):

| setting | values (default first) |
|---|---|
| `FLUSHES` | `endflush`: no flush per step, every rank fsyncs the file after closing it. `flush`: H5Fflush (fsync) every step, as in the paper. `noflush`: none |
| `PLACEMENTS` | `shared`: one hardware thread per rank. `iocore`: two cores per rank, main thread pinned to the first and every other thread (the async VOL's execution stream) to the second. `reserved` (not by default): two cores, placed by the OS |
| `KERNELS` | sleep dgemm stream spin; also halo net |

The async VOL always runs H5Fflush as a blocking task, so with `flush` the
main thread waits for each step's write inside H5Fflush_async and the write
does not overlap the next compute phase. With `endflush` and `noflush` it
does, which is where interference with the compute shows up.

Two async VOL settings are needed for the writes to overlap at all:

- `ASYNC_START=1` (default) calls `H5VL_async_start()` after opening the file.
  Without it the async VOL hands a queued write to its thread only when the
  application next blocks (the next step's H5ESwait), and now and then one
  rank's collective task never starts and the job hangs. Runs with
  `ASYNC_START=0` get tag `.nostart`.
- `HDF5_ASYNC_MAX_MEM_MB` (`ASYNC_MAX_MEM_MB`, default `MB`+256). Past this
  much copied buffer data the async VOL writes synchronously, without saying
  so. Unset, it is the free RAM when the process starts, which on a node with
  a full page cache can be less than one step.

`hdf5_summary.py` gives the medians of the untraced runs, with the async
compute time against both the sync compute time on the same placement and the
standalone run. Use the first: the standalone run is a few seconds measured
once per case and can be off by 10% (on the laptop sync dgemm came out at
0.86x of it). It also gives observed I/O, and the speedup that five models
predict (naive overlap, Ravi et al., the sleep run, serial, blend; see its
docstring). `../../analysis/models.py` checks those models, and models that use
the first steps of a run or its traces, against every machine's runs.

Traced runs (tag `<placement>.trace` in the CSV; the untraced repetitions give
the timings, since tracing changes the shared async runs: DFTracer and the
sampler share the one hardware thread, and the async writes stop overlapping,
110 s against 177 s for sleep) write to `results/perlmutter/hdf5_traces/hdf5_trace_n2/<placement>.<kernel>.<mode>.<flush>/`,
which is committed with the results. The `perfetto.json` that `hdf5_traces.py`
makes in each case directory is git-ignored; run it again to get them back.

- `dft-*.pfw.gz`: DFTracer, one file per rank. POSIX calls on the output
  file, MPI and MPI-IO calls, and HDF5 API calls, from every thread.
  Calls shorter than 100 us are aggregated into counts.
- `aio.<rank>.phases.csv.gz`: the main thread's compute, HDF5 calls and barriers.
- `aio.<rank>.threads.csv.gz`: every thread sampled each `SAMPLE_MS` (5) ms, with
  the CPU it is on, its run time and its run-queue wait from
  `/proc/self/task/<tid>/schedstat`.

`hdf5_traces.py` combines them. For each compute phase it reports the
stretch over the standalone run, the main thread's run-queue wait, the I/O
thread's CPU time (and how much of it was on the main thread's CPU), and how
much of the phase overlapped I/O calls. It writes
`results/perlmutter/hdf5_trace_n2_summary.csv`, a `perfetto.json` per case
(open in ui.perfetto.dev) and `figures/perlmutter/hdf5_trace_n2/hdf5_trace_<kernel>_<flush>.png`.

A case that does not finish in `CASE_TIMEOUT` (600 s) is logged as HANG, its
run directory kept, and it is run again up to `HANG_RETRIES` (1) times.
