#!/usr/bin/env python3
"""Run the experiment matrix for the paper, phase by phase.

    run_matrix.py [phase ...]      phases: io0 main size tera sweep bind gpu
                                    (default: all; breadth-first over repetitions)

Each phase appends to results/<phase>.csv and skips configurations already
present there, so the script can be interrupted and restarted.  The compute
length t_comp of a configuration is derived from the synchronous I/O-only
time measured in phase io0 (t_comp = ratio * t_io_sync per step).
"""
import csv
import os
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RES = os.path.join(ROOT, "results")
RUN = os.path.join(ROOT, "src", "scripts", "run_one.sh")
LOG = os.path.join(RES, "run_matrix.log")

BACKENDS = ["posix", "mpiio", "hdf5", "adios2"]
CPU_KERNELS = ["sleep", "spin", "dgemm", "stream", "halo", "heat"]
RATIO = 1.25          # default t_comp / t_io_sync
NP = 48               # one rank per physical core
ENV = {
    "AIO_ADIOS2_PARAMS": "AggregationType=TwoLevelShm;NumAggregators=8",
    "AIO_HDF5_INDEP": "0",
    "AIO_HDF5_EXE_DCLOSE": "1",
    "AIO_ROMIO_HINTS": "cb_config_list=*:8,cb_nodes=8",
}


def log(msg):
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def memmax(mb, np_):
    # buffer + staging/library copy + kernel arrays per rank, plus page cache
    anon_gb = np_ * (2.2 * mb + 120) / 1024
    return f"{int(min(230, anon_gb + 24))}G"


def done_keys(path):
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        return {key_of(r) for r in csv.DictReader(f)}


def key_of(r):
    return (r["io"], r["mode"], r["kernel"], r["tag"], r["nranks"],
            f'{float(r["mib_per_rank"]):.1f}', r["steps"], f'{float(r["tcomp_target"]):.3f}')


def run(phase, bind, np_, io, mode, kernel, mb, steps, tcomp, extra=(), env=None, rep=0):
    tcomp = round(tcomp, 2) + rep * 1e-3  # repetition index encoded in the key
    path = os.path.join(RES, f"{phase}.csv")
    key = (io, mode, kernel, bind, str(np_), f"{float(mb):.1f}", str(steps), f"{float(tcomp):.3f}")
    if key in done_keys(path):
        return
    e = dict(os.environ, **ENV, AIO_CSV=path, AIO_MEMMAX=memmax(mb, np_))
    if env:
        e.update(env)
    cmd = [RUN, bind, str(np_), io, mode, kernel, "--mb", str(mb), "--steps", str(steps),
           "--tcomp", f"{tcomp:.3f}", "--no-fsync", "--alone-reps", "2", *extra]
    t0 = time.time()
    p = subprocess.run(cmd, env=e, capture_output=True, text=True, timeout=7200)
    tail = (p.stderr.strip().splitlines() or ["?"])[-1]
    log(f"[{phase}] rc={p.returncode} {time.time() - t0:6.1f}s {tail}")
    if p.returncode:
        with open(LOG, "a") as f:
            f.write(p.stdout[-3000:] + p.stderr[-3000:] + "\n")


def tio_sync(io, mb, np_=NP, phase="io0", steps=None, tag="full", rep=None):
    """Per-step synchronous I/O time (end-to-end / steps) of the I/O-only
    run with the same backend, size, ranks and step count, taken from
    `phase` when present there (repetition `rep` only, so t_comp is
    deterministic across restarts), otherwise from io0."""
    for ph in (phase, "io0"):
        path = os.path.join(RES, f"{ph}.csv")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            rows = [r for r in csv.DictReader(f) if r["io"] == io and r["mode"] == "sync"
                    and r["kernel"] == "none" and int(r["nranks"]) == np_ and r["tag"] == tag
                    and abs(float(r["mib_per_rank"]) - mb) < 0.1
                    and (steps is None or ph == "io0" or int(r["steps"]) == steps)
                    and (rep is None or ph == "io0"
                         or round(float(r["tcomp_target"]) * 1000) == rep)]
        if rows:
            return sum(float(r["t_total"]) / int(r["steps"]) for r in rows) / len(rows)
    raise SystemExit(f"no I/O-only baseline for {io} {mb} MiB P={np_}")


def baseline(phase, bind, np_, io, mb, steps, rep=0):
    """I/O-only sync and async runs of this exact configuration; returns the
    per-step synchronous I/O time used to size t_comp."""
    for mode in ("sync", "async"):
        run(phase, bind, np_, io, mode, "none", mb, steps, 0, rep=rep)
    return tio_sync(io, mb, np_, phase=phase, steps=steps, tag=bind, rep=rep)


SIZES = [16, 64, 256, 1024]   # MiB per rank; x48 ranks = 0.75 .. 48 GiB per step


def phase_io0(rep):
    """Synchronous/asynchronous I/O-only baselines (5 steps) for every size."""
    for mb in SIZES:
        for io in BACKENDS:
            for mode in ("sync", "async"):
                run("io0", "full", NP, io, mode, "none", mb, 5, 0, rep=rep)
    for io in BACKENDS:              # GPU configuration: 4 ranks
        for mode in ("sync", "async"):
            run("io0", "full", 4, io, mode, "none", 1024, 5, 0, rep=rep)
    for io in BACKENDS:              # reserved-core configuration: 24 ranks
        run("io0", "reserved", 24, io, "sync", "none", 512, 5, 0, rep=rep)


def phase_main(rep):
    """RQ1/RQ2: every CPU surrogate, 12 GiB/step x 10 steps = 120 GiB."""
    mb, steps = 256, 10
    for io in BACKENDS:
        tc = RATIO * baseline("main", "full", NP, io, mb, steps, rep)
        for k in CPU_KERNELS:
            for mode in ("sync", "async"):
                run("main", "full", NP, io, mode, k, mb, steps, tc, rep=rep)


def phase_size(rep):
    """Size sweep: 0.75 .. 48 GiB per step, 10 steps (7.5 GiB .. 480 GiB)."""
    for mb in SIZES:
        for io in BACKENDS:
            tc = RATIO * baseline("size", "full", NP, io, mb, 10, rep)
            for k in ["sleep", "dgemm", "stream", "heat"]:
                for mode in ("sync", "async"):
                    run("size", "full", NP, io, mode, k, mb, 10, tc, rep=rep)


def phase_tera(rep):
    """~1 TB of output: 48 GiB/step x 20 steps = 960 GiB."""
    mb = 1024
    for io in BACKENDS:
        tc = RATIO * baseline("tera", "full", NP, io, mb, 20, rep)
        for k in ["sleep", "stream", "heat"]:
            for mode in ("sync", "async"):
                run("tera", "full", NP, io, mode, k, mb, 20, tc, rep=rep)


def phase_sweep(rep):
    """t_comp / t_io sweep, 12 GiB/step; t_io from the main phase (rep 0)."""
    mb = 256
    for io in BACKENDS:
        t = tio_sync(io, mb, phase="main", steps=10, rep=0)
        for r in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0):
            for k in ["sleep", "dgemm", "stream"]:
                for mode in ("sync", "async"):
                    run("sweep", "full", NP, io, mode, k, mb, 10, r * t, rep=rep)


def phase_bind(rep):
    """RQ4a: core allocation.  Same 12 GiB per step in every configuration."""
    for io in BACKENDS:
        tc = RATIO * tio_sync(io, 256, phase="main", steps=10, rep=0)
        for bind in ("smt", "none"):
            baseline("bind", bind, NP, io, 256, 10, rep)
        baseline("bind", "reserved", 24, io, 512, 10, rep)
        for k in ["sleep", "dgemm", "stream"]:
            for mode in ("sync", "async"):
                for bind in ("full", "smt", "none"):
                    run("bind", bind, NP, io, mode, k, 256, 10, tc, rep=rep)
                run("bind", "reserved", 24, io, mode, k, 512, 10, tc, rep=rep)


def phase_gpu(rep):
    """RQ4b: GPU-resident compute, 4 ranks sharing the GPU, 4 GiB/step."""
    for io in BACKENDS:
        tc = RATIO * baseline("gpu", "full", 4, io, 1024, 10, rep)
        for k in ["sleep", "gpu-block", "gpu-spin", "dgemm"]:
            for mode in ("sync", "async"):
                run("gpu", "full", 4, io, mode, k, 1024, 10, tc, rep=rep)


def phase_sizelarge(rep):
    """48 GiB per step (480 GiB per run).  POSIX and ADIOS2 ran first; MPI-IO
    and HDF5 (30 to 40 minutes per run at this size) were added afterwards."""
    for io in ["posix", "adios2", "mpiio", "hdf5"]:
        tc = RATIO * baseline("size", "full", NP, io, 1024, 10, rep)
        for k in ["sleep", "dgemm", "stream", "heat"]:
            for mode in ("sync", "async"):
                run("size", "full", NP, io, mode, k, 1024, 10, tc, rep=rep)


def phase_recheck(rep):
    """Re-run the main configuration's synchronous sleep and spin runs, with
    spin first, to test whether the sleep run's slower synchronous I/O in the
    main phase was caused by its position right after the baselines."""
    for io in BACKENDS:
        tc = RATIO * tio_sync(io, 256, phase="main", steps=10, rep=0)
        for k in ("spin", "sleep"):
            run("recheck", "full", NP, io, "sync", k, 256, 10, tc, rep=rep)


RUN_PDC = os.path.join(ROOT, "src", "scripts", "run_pdc.sh")


def run_pdc(phase, ns, nc, mode, kernel, mb, steps, tcomp, rep=0, smt=False):
    """Like run(), but through run_pdc.sh (PDC servers on reserved cores).
    smt=True also gives every client its SMT sibling (tag pdc<ns>smt)."""
    tcomp = round(tcomp, 2) + rep * 1e-3
    path = os.path.join(RES, f"{phase}.csv")
    tag = f"pdc{ns}" + ("smt" if smt else "")
    key = ("pdc", mode, kernel, tag, str(nc), f"{float(mb):.1f}", str(steps), f"{float(tcomp):.3f}")
    if key in done_keys(path):
        return
    e = dict(os.environ, AIO_CSV=path, AIO_MEMMAX=memmax(mb, nc), AIO_PDC_TAG=tag)
    if smt:
        e["AIO_PDC_CLIENT_CPUS"] = ",".join(f"{c}+{c + 48}" for c in range(ns, ns + nc))
    cmd = [RUN_PDC, str(ns), str(nc), mode, kernel, "--mb", str(mb), "--steps", str(steps),
           "--tcomp", f"{tcomp:.3f}", "--no-fsync", "--alone-reps", "2"]
    t0 = time.time()
    # Own process group, so a hung run (server, clients, mpiexec) can be
    # killed as a whole instead of leaving orphans that block later runs.
    pr = subprocess.Popen(cmd, env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, start_new_session=True)
    try:
        out, err = pr.communicate(timeout=PDC_TIMEOUT)
        rc = pr.returncode
    except subprocess.TimeoutExpired:
        os.killpg(pr.pid, 9)
        out, err = pr.communicate()
        subprocess.run(["pkill", "-9", "-x", "pdc_server"])
        subprocess.run(["pkill", "-9", "-x", "aio_bench_pdc"])
        rc, err = "timeout", (err or "") + f"\nkilled after {PDC_TIMEOUT} s"
    tail = (err.strip().splitlines() or ["?"])[-1]
    log(f"[{phase}] rc={rc} {time.time() - t0:6.1f}s {tail}")
    if rc != 0:
        with open(LOG, "a") as f:
            f.write(out[-3000:] + err[-3000:] + "\n")


PDC_TIMEOUT = 1800  # seconds per PDC run


def phase_pdc(rep):
    """PDC in its deployment model: 1 server on a reserved core (and its SMT
    sibling), 47 clients on the remaining cores, 256 MiB per client per step,
    10 steps, every CPU surrogate."""
    ns, nc, mb, steps = 1, 47, 256, 10
    for mode in ("sync", "async"):
        run_pdc("pdc", ns, nc, mode, "none", mb, steps, 0, rep=rep)
    t = tio_sync("pdc", mb, nc, phase="pdc", steps=steps, tag=f"pdc{ns}", rep=rep)
    for k in CPU_KERNELS:
        for mode in ("sync", "async"):
            # Async halo with the default placement did not finish in 30
            # minutes in two attempts (time slicing with the client progress
            # thread); it is measured with the SMT placement below instead.
            if (k, mode) != ("halo", "async"):
                run_pdc("pdc", ns, nc, mode, k, mb, steps, RATIO * t, rep=rep)
    # The client's Mercury progress thread polls on the rank's core; with the
    # SMT sibling free for it, does the async slowdown go away?
    for k in ("sleep", "spin", "dgemm", "stream", "halo", "heat"):
        run_pdc("pdc", ns, nc, "async", k, mb, steps, RATIO * t, rep=rep, smt=True)


def pdc_baseline(phase, ns, nc, mb, steps, rep=0):
    """PDC I/O-only sync and async runs; returns the sync I/O time per step."""
    for mode in ("sync", "async"):
        run_pdc(phase, ns, nc, mode, "none", mb, steps, 0, rep=rep)
    return tio_sync("pdc", mb, nc, phase=phase, steps=steps, tag=f"pdc{ns}", rep=rep)


def phase_pdcgpu(rep):
    """PDC with GPU-resident compute: 1 server, 4 clients sharing the GPU,
    4 GiB per step, as in phase_gpu."""
    ns, nc, mb, steps = 1, 4, 1024, 10
    tc = RATIO * pdc_baseline("pdcgpu", ns, nc, mb, steps, rep)
    for k in ["sleep", "gpu-block", "gpu-spin", "dgemm"]:
        for mode in ("sync", "async"):
            run_pdc("pdcgpu", ns, nc, mode, k, mb, steps, tc, rep=rep)


def phase_pdcsize(rep):
    """PDC at 0.73 and 2.9 GiB per step (47 clients); 11.75 GiB is the main
    experiment.  47 GiB per step takes about 90 minutes per run through one
    server and is not run."""
    ns, nc, steps = 1, 47, 10
    for mb in (16, 64):
        tc = RATIO * pdc_baseline("pdcsize", ns, nc, mb, steps, rep)
        for k in ["sleep", "dgemm", "stream", "heat"]:
            for mode in ("sync", "async"):
                run_pdc("pdcsize", ns, nc, mode, k, mb, steps, tc, rep=rep)


def phase_pdcsweep(rep):
    """PDC t_comp / t_io sweep at 11.75 GiB per step; 1.25 is the main
    experiment."""
    ns, nc, mb, steps = 1, 47, 256, 10
    t = tio_sync("pdc", mb, nc, phase="pdc", steps=steps, tag=f"pdc{ns}", rep=0)
    for r in (0.25, 0.5, 1.0):
        for k in ["sleep", "dgemm", "stream"]:
            for mode in ("sync", "async"):
                run_pdc("pdcsweep", ns, nc, mode, k, mb, steps, r * t, rep=rep)


# Default order: cheaper phases first so every figure gets data early.
PHASES = {"io0": phase_io0, "main": phase_main, "gpu": phase_gpu, "bind": phase_bind,
          "sweep": phase_sweep, "size": phase_size, "tera": phase_tera,
          "sizelarge": phase_sizelarge, "recheck": phase_recheck, "pdc": phase_pdc,
          "pdcgpu": phase_pdcgpu, "pdcsize": phase_pdcsize, "pdcsweep": phase_pdcsweep}
# Repetitions per phase.  Execution is breadth-first: repetition 0 of every
# phase runs before repetition 1 of any, so every result gets data early.
# One run per configuration (repetitions are future work); io0 was run twice.
REPS_OF = {"io0": 2, "main": 1, "size": 1, "tera": 1, "sweep": 1, "bind": 1, "gpu": 1,
           "sizelarge": 1, "recheck": 1, "pdc": 1, "pdcgpu": 1, "pdcsize": 1,
           "pdcsweep": 1}

if __name__ == "__main__":
    os.makedirs(RES, exist_ok=True)
    order = sys.argv[1:] or list(PHASES)
    for rep in range(max(REPS_OF[p] for p in order)):
        for ph in order:
            if rep < REPS_OF[ph]:
                log(f"=== phase {ph} rep {rep}")
                PHASES[ph](rep)
    log("=== all requested phases done")
