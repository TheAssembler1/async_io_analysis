#!/usr/bin/env python3
"""Models of the async run time, checked against every aio_bench run in results/.

    models.py [prior] [online] [post]       default: all three

prior   Before the run, from the sync run and an async sleep run (the "probe")
        on the same placement. 117 sync/async pairs: the 48-core node
        (results/local: four backends, four placements, 16 MiB to 1 GiB),
        Perlmutter and the laptop (HDF5, untraced runs).
online  While the run goes, from its first k async steps (per-step times in
        the phase traces). Also whether staying async is the right call.
post    After the run, from the traced run's CPU accounting
        (<traces>_summary.csv from hdf5_traces.py): what explains the compute
        slowdown.

Per step, c and i are the sync run's compute and observed I/O time, n the step
count; o and f are the probe's blocking I/O per step and end-of-run drain (last
write, close, fsync). Errors are measured async time over predicted - 1, which
is also the error of the predicted speedup. Times are as aio_bench reports them
(per-step max over ranks).

Prior models:
  naive   perfect overlap, no cost:  n*max(c, i) + min(c, i)
  Ravi    Ravi et al., IPDPS 2023: plus a per-step transaction overhead,
          n*(max(c, i) + o) + min(c, i)
  probe   the probe's run time plus the difference in standalone compute
  serial  a CPU-bound kernel on the async thread's hardware thread gets no
          overlap: the sync time plus the overhead, n*o + T_sync; else Ravi
          with the probe's drain, n*(max(c, i) + o) + f
  blend   Ravi with the probe's drain, moved a fraction phi of the way to
          serial for CPU-bound kernels; one phi per placement class, fitted on
          the 48-core node (PHI)

Most cases on the 48-core node ran once, and repeated runs there differ by a
median of 8%, so errors below that do not tell the models apart.
"""
import csv
import glob
import gzip
import os
import sys
from collections import defaultdict
from statistics import mean, median

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "..", "results")
RUNS = {
    "local": ["local/main.csv", "local/bind.csv", "local/size.csv"],
    "perlmutter": ["perlmutter/hdf5_trace_n2.csv"],
    "laptop": ["laptop/hdf5_endflush_n1.csv"],
}
TRACES = {
    "perlmutter": "perlmutter/hdf5_traces/hdf5_trace_n2",
    "laptop": "laptop/hdf5_traces/hdf5_endflush_n1",
}
SUMMARIES = {
    "laptop 1 GiB": "laptop/hdf5_endflush_n1_summary.csv",
    "laptop 64 MiB": "laptop/hdf5_trace_n1_summary.csv",
    "perlmutter": "perlmutter/hdf5_trace_n2_summary.csv",
}
# where the async thread runs relative to the main thread
CLASS = {"full": "shared", "shared": "shared", "smt": "smt", "reserved": "core", "iocore": "core",
         "none": "unbound"}
# machines where iocore's second CPU is the SMT sibling of the first (the
# laptop's CPUs 0 and 1), so the async thread shares the core
IOCORE_IS_SIBLING = {"laptop 1 GiB", "laptop 64 MiB"}
IDLE_KERNELS = {"sleep"}  # every other kernel keeps its CPU busy (MPI waits poll)


# ---------------------------------------------------------------- prior

def load_pairs():
    g = defaultdict(list)
    for ds, files in RUNS.items():
        for f in files:
            for r in csv.DictReader(open(os.path.join(RESULTS, f))):
                parts = r["tag"].split(".")
                if "trace" in parts:  # tracing changes shared runs; see README
                    continue
                grp = (ds, r["io"], parts[0], ".".join(p for p in parts[1:] if p != "start"),
                       float(r["mib_per_rank"]), int(r["nranks"]), int(r["steps"]))
                g[grp + (r["kernel"], r["mode"])].append(r)

    def m(rows, col):
        return median(float(r[col]) for r in rows)

    pairs = []
    for key, a in g.items():
        *grp, k, mode = key
        s, p = g.get(tuple(grp) + (k, "sync")), g.get(tuple(grp) + ("sleep", "async"))
        if mode != "async" or k == "none" or not s or not p:
            continue
        ds, io, place, _, mb, nranks, n = grp
        pairs.append(dict(
            ds=ds, io=io, place=place, cls=CLASS[place], mb=mb, k=k, n=n, busy=k not in IDLE_KERNELS,
            c=m(s, "t_comp"), i=m(s, "t_io_obs") / n, Ts=m(s, "t_total"), Ta=m(a, "t_total"),
            alone=m(a, "t_alone"), o=m(p, "t_io_step"), f=m(p, "t_final"), Tp=m(p, "t_total"),
            alone_p=m(p, "t_alone")))
    return pairs


def naive(x):
    return x["n"] * max(x["c"], x["i"]) + min(x["c"], x["i"])


def ravi(x):
    return x["n"] * (max(x["c"], x["i"]) + x["o"]) + min(x["c"], x["i"])


def ravi_drain(x):
    return x["n"] * (max(x["c"], x["i"]) + x["o"]) + x["f"]


def probe(x):
    return x["Tp"] + x["n"] * (x["alone"] - x["alone_p"])


def serial_bound(x):
    return x["Ts"] + x["n"] * x["o"]


def serial(x):
    return serial_bound(x) if x["cls"] == "shared" and x["busy"] else ravi_drain(x)


def fit_phi(pairs):
    """Least-squares phi per placement class, for busy kernels."""
    phi = {}
    for cls in {x["cls"] for x in pairs}:
        sel = [x for x in pairs if x["cls"] == cls and x["busy"]]
        d = np.array([serial_bound(x) - ravi_drain(x) for x in sel])
        r = np.array([x["Ta"] - ravi_drain(x) for x in sel])
        phi[cls] = float(d @ r / (d @ d)) if len(sel) and d @ d > 0 else 0.0
    return phi


def blend(x, phi):
    b = ravi_drain(x)
    return b + phi.get(x["cls"], 0.0) * (serial_bound(x) - b) if x["busy"] else b


def err(x, t):
    return (x["Ta"] / t - 1) * 100


def report_prior():
    pairs = load_pairs()
    groups = defaultdict(list)
    for j, x in enumerate(pairs):
        groups[x["ds"]].append(j)
        groups[x["cls"] + (" busy" if x["busy"] else " sleep")].append(j)
    cols = sorted(groups)
    print(f"## Prior models: {len(pairs)} sync/async pairs, mean |error| %\n")
    print("| model | all | max | " + " | ".join(f"{c} ({len(groups[c])})" for c in cols) + " |")
    print("|---" * (len(cols) + 3) + "|")

    def row(name, e):
        e = np.abs(np.array(e))
        print(f"| {name} | {e.mean():.1f} | {e.max():.0f} | " + " | ".join(f"{e[groups[c]].mean():.1f}" for c in cols) + " |")

    for name, f in [("naive", naive), ("Ravi", ravi), ("probe", probe), ("serial", serial)]:
        row(name, [err(x, f(x)) for x in pairs])
    # blend: leave each pair out of its own fit
    row("blend, leave-one-out", [err(x, blend(x, fit_phi(pairs[:j] + pairs[j + 1:]))) for j, x in enumerate(pairs)])
    local = fit_phi([x for x in pairs if x["ds"] == "local"])
    row("blend, phi from the 48-core node", [err(x, blend(x, local)) for x in pairs])
    print("\nphi fitted on the 48-core node: " + ", ".join(f"{k} {v:.2f}" for k, v in sorted(local.items())))


# ---------------------------------------------------------------- online

def opentext(p):
    return gzip.open(p, "rt") if p.endswith(".gz") else open(p)


def load_steps(d):
    """Per-step compute and I/O (issue + barrier) time, max over ranks, and the drain."""
    comp, io, drain = defaultdict(float), defaultdict(float), 0.0
    for f in glob.glob(os.path.join(d, "aio.*.phases.csv*")):
        per = defaultdict(lambda: defaultdict(float))
        for r in csv.DictReader(opentext(f)):
            per[r["name"]][int(r["step"])] += float(r["dur_us"]) / 1e6
        for s, v in per["compute"].items():
            comp[s] = max(comp[s], v)
        for s, v in per["issue"].items():
            io[s] = max(io[s], v + per["barrier"].get(s, 0.0))
        drain = max(drain, sum(per["drain"].values()))
    n = len(comp)
    return [comp[s] for s in range(n)], [io[s] for s in range(n)], drain


def ewma(v, a=0.5):
    e = v[0]
    for x in v[1:]:
        e = a * x + (1 - a) * e
    return e


def online(comp, io, n, k, c0, i0):
    """Predicted total after k async steps; c0, i0 from one sync step, and one
    sync I/O step stands in for the drain."""
    step = [a + b for a, b in zip(comp[:k], io[:k])]
    done = sum(step)
    cc, o = ewma(comp[:k]), ewma(io[:k])
    return {"mean step": done + (n - k) * mean(step) + i0,
            "mean step, skip step 0": done + (n - k) * mean(step[1:] or step) + i0,
            "Ravi online (EWMA)": done + (n - k) * (max(cc, i0 - cc) + o) + i0}


def report_online():
    runs = {}
    for ds, rel in TRACES.items():
        for d in sorted(glob.glob(os.path.join(RESULTS, rel, "*.*.*.*"))):
            if glob.glob(os.path.join(d, "aio.*.phases.csv*")):
                place, k, mode, flush = os.path.basename(d).split(".")[:4]
                runs[(ds, place, k, mode, flush)] = load_steps(d)
    res = defaultdict(list)
    for (ds, place, k, mode, flush), (comp, io, drain) in runs.items():
        sync = runs.get((ds, place, k, "sync", flush))
        if mode != "async" or not sync:
            continue
        n, ta = len(comp), sum(comp) + sum(io) + drain
        ts = sum(sync[0]) + sum(sync[1]) + sync[2]
        c0, i0 = sync[0][0], sync[1][0]
        for steps in (1, 2, 3):
            for name, t in online(comp, io, n, steps, c0, i0).items():
                res[(name, steps)].append(((ta / t - 1) * 100, (t < n * (c0 + i0)) == (ta < ts), ds))
    nruns = len(next(iter(res.values())))
    print(f"\n## Online models: {nruns} traced async runs, mean |error| % (max), "
          "and how often stay-async-or-not is right\n")
    print("| model | steps seen | all | Perlmutter | laptop | decision |")
    print("|---|---|---|---|---|---|")
    for (name, steps), v in sorted(res.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        e = [abs(x[0]) for x in v]
        by = {ds: mean(abs(x[0]) for x in v if x[2] == ds) for ds in ("perlmutter", "laptop")}
        print(f"| {name} | {steps} | {mean(e):.1f} ({max(e):.0f}) | {by['perlmutter']:.1f} | {by['laptop']:.1f} "
              f"| {sum(x[1] for x in v)}/{len(v)} |")


# ---------------------------------------------------------------- post

def report_post():
    rows = []
    for ds, rel in SUMMARIES.items():
        p = os.path.join(RESULTS, rel)
        if not os.path.exists(p):
            print(f"\n(no {rel}; run hdf5_traces.py)")
            continue
        summ = list(csv.DictReader(open(p)))
        sync = {(r["flush"], r["placement"], r["kernel"]): r for r in summ if r["mode"] == "sync"}
        for r in summ:
            s = sync.get((r["flush"], r["placement"], r["kernel"]))
            if r["mode"] != "async" or not s:
                continue
            v = lambda x, c: float(x[c])
            other = v(r, "iocpu_ms") - v(r, "same_ms")
            sib = ds in IOCORE_IS_SIBLING and r["placement"] == "iocore"
            rows.append(dict(ds=ds, case=f"{r['placement']} {r['kernel']} {r['flush']}", comp=v(s, "comp_ms"),
                             slow=v(r, "comp_ms") - v(s, "comp_ms"), wait=v(r, "wait_ms") - v(s, "wait_ms"),
                             same=v(r, "same_ms"), other=other, sibling=other if sib else 0.0,
                             core=0.0 if sib else other))
    y = np.array([r["slow"] for r in rows])
    scale = np.array([r["comp"] for r in rows])
    print(f"\n## Post models: {len(rows)} traced async runs, compute slowdown per phase explained by\n")
    print("| case | slowdown ms | run-queue wait ms | I/O CPU on main's CPU ms | I/O CPU elsewhere ms |")
    print("|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['ds']} {r['case']} | {r['slow']:.0f} | {r['wait']:.0f} | {r['same']:.0f} | {r['other']:.0f} |")
    dss = sorted({r["ds"] for r in rows})
    print("\nmean |error| in % of the sync compute phase:\n")
    print("| model | all | max | " + " | ".join(dss) + " | coefficients (all runs) |")
    print("|---" * (len(dss) + 4) + "|")

    def row(name, pred, coef=""):
        e = np.abs(pred - y) / scale * 100
        by = " | ".join(f"{np.mean([x for x, r in zip(e, rows) if r['ds'] == d]):.1f}" for d in dss)
        print(f"| {name} | {e.mean():.1f} | {e.max():.0f} | {by} | {coef} |")

    row("no slowdown", np.zeros_like(y))
    row("run-queue wait", np.array([r["wait"] for r in rows]))
    row("I/O CPU on main's CPU", np.array([r["same"] for r in rows]))
    for feats in (["wait", "other"], ["wait", "sibling", "core"], ["same", "sibling", "core"]):
        X = np.array([[r[f] for f in feats] for r in rows])
        pred = np.array([X[j] @ np.linalg.lstsq(np.delete(X, j, 0), np.delete(y, j), rcond=None)[0]
                         for j in range(len(rows))])
        b = np.linalg.lstsq(X, y, rcond=None)[0]
        row(f"fit {' + '.join(feats)}, leave-one-out", pred, ", ".join(f"{f} {c:.2f}" for f, c in zip(feats, b)))


if __name__ == "__main__":
    what = sys.argv[1:] or ["prior", "online", "post"]
    for w in what:
        {"prior": report_prior, "online": report_online, "post": report_post}[w]()
