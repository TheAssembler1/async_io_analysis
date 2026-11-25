#!/usr/bin/env python3
"""Medians over repetitions from the CSV of an HDF5 job (scripts/perlmutter/hdf5.sh).

    hdf5_summary.py [csv ...] [--traced]    default: results/perlmutter/hdf5_trace_n*.csv

One row per flush mode x placement x kernel. Traced runs (tag .trace) are left
out unless --traced. The compute slowdown of async is given against sync
compute on the same placement as well as against the standalone run: the
standalone run is a few seconds measured once per case, and on a machine whose
clock speed drifts (turbo, thermals) it can be off by 10% or more, which shows
up as sync compute below 1x.

Observed I/O is t_io_obs: the time the main thread spends outside compute
(issuing the I/O and the barrier after it every step, plus the final wait),
max over ranks. I/O speedup is sync over async observed I/O. Compute, I/O and
total times are for the whole run. Predicted speedup is the naive model's:
perfect overlap and no interference, so with the sync run's per-step compute c
and I/O i over n steps the async run takes n*max(c, i) + min(c, i) (only the
last step's I/O is exposed when c > i), against the sync total. Two more
predictions, both from the async sleep run on the same placement:
- Ravi et al. (IPDPS 2023): the naive model plus a per-step transaction
  overhead (task creation, buffer copy), n*(max(c, i) + o) + min(c, i), with o
  the sleep run's per-step observed I/O.
- sleep: the async sleep run's total plus the difference in standalone compute,
  T_async(sleep) + n*(t_alone(k) - t_alone(sleep)), as in the paper's metrics.
- serial: a busy kernel on the async thread's hardware thread (shared) gets no
  overlap, n*o + T_sync; otherwise Ravi with the sleep run's final drain f,
  n*(max(c, i) + o) + f.
- blend: Ravi with the drain, moved a fraction phi of the way to serial for
  busy kernels, with phi per placement fitted on the 48-core node runs
  (analysis/models.py).
"closest" is the model nearest the measured speedup, with its error. For the
sleep kernel the sleep model is its own measurement and is left out.
"""
import csv
import glob
import os
import sys
from collections import defaultdict
from statistics import median

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import models as m_  # noqa: E402

PHI = m_.fit_phi([x for x in m_.load_pairs() if x["ds"] == "local"])

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "results", "perlmutter")
args = [a for a in sys.argv[1:] if not a.startswith("--")]
traced = "--traced" in sys.argv
files = args or sorted(f for f in glob.glob(os.path.join(root, "hdf5_trace_n*.csv")))
if not files:
    sys.exit(f"no CSV given and none in {root}")

g = defaultdict(list)
for f in files:
    for r in csv.DictReader(open(f)):
        if r["io"] != "hdf5" or r["kernel"] == "none":
            continue
        parts = r["tag"].split(".")
        if "trace" in parts and not traced:
            continue
        # tag = <placement>.<flush>[.start|.nostart][.trace]
        g[(parts[1], parts[0], r["kernel"], r["mode"], ".".join(p for p in parts[2:] if p != "trace"))].append(r)


def med(key, col):
    # t_comp is the mean per step; scale it to the run like the other times
    v = [float(r[col]) * (int(r["steps"]) if col == "t_comp" else 1) for r in g.get(key, [])]
    return median(v) if v else float("nan")


print("| flush | placement | kernel | variant | runs | sync comp s | async comp s | async/sync comp "
      "| async/alone | sync/alone | sync I/O s | async I/O s | I/O speedup | sync total s | async total s | total speedup | naive model | Ravi model | sleep model | serial model | blend model | closest |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
order = {"shared": 0, "reserved": 1, "iocore": 2}
for flush, place, k, var in sorted({(f, p, k, v) for f, p, k, m, v in g if m == "async"},
                                   key=lambda x: (x[0], x[2], order.get(x[1], 9), x[3])):
    s, a = (flush, place, k, "sync", ""), (flush, place, k, "async", var)
    cs, ca, ts, ta = med(s, "t_comp"), med(a, "t_comp"), med(s, "t_total"), med(a, "t_total")
    io_s, io_a = med(s, "t_io_obs"), med(a, "t_io_obs")
    n = int(g[a][0]["steps"])
    c, i = cs / n, io_s / n
    pred = n * max(c, i) + min(c, i)
    sl = (flush, place, "sleep", "async", var)
    ravi = n * (max(c, i) + med(sl, "t_io_step")) + min(c, i)
    sleep = med(sl, "t_total") + n * (med(a, "t_alone") - med(sl, "t_alone"))
    busy = k not in m_.IDLE_KERNELS
    x = dict(n=n, c=c, i=i, o=med(sl, "t_io_step"), f=med(sl, "t_final"), Ts=ts, cls=m_.CLASS[place], busy=busy)
    serial, blend = m_.serial(x), m_.blend(x, PHI)
    models = {"naive": ts / pred, "Ravi": ts / ravi, "sleep": ts / sleep, "blend": ts / blend,
              "serial": ts / serial}  # serial = blend for idle kernels; ties go to blend
    if k == "sleep":
        del models["sleep"]
    best = min(models, key=lambda m: abs(models[m] - ts / ta))
    err = (models[best] / (ts / ta) - 1) * 100
    print(f"| {flush} | {place} | {k} | {var or '-'} | {len(g.get(s, []))}/{len(g[a])} | {cs:.1f} | {ca:.1f} "
          f"| {ca / cs:.2f}x | {med(a, 'slowdown'):.2f}x | {med(s, 'slowdown'):.2f}x | {io_s:.1f} | {io_a:.1f} | {io_s / io_a:.2f}x | {ts:.1f} | {ta:.1f} "
          f"| {ts / ta:.2f}x | {ts / pred:.2f}x | {ts / ravi:.2f}x | {ts / sleep:.2f}x | {ts / serial:.2f}x | {ts / blend:.2f}x | {best} ({err:+.0f}%) |")
