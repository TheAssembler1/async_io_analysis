#!/usr/bin/env python3
"""Medians over repetitions from the CSV of an HDF5 job (scripts/perlmutter/hdf5.sh).

    hdf5_summary.py [csv ...] [--traced]    default: results/perlmutter/hdf5_trace_n*.csv

One row per flush mode x placement x kernel. Traced runs (tag .trace) are left
out unless --traced. The compute slowdown of async is given against sync
compute on the same placement as well as against the standalone run: the
standalone run is a few seconds measured once per case, and on a machine whose
clock speed drifts (turbo, thermals) it can be off by 10% or more, which shows
up as sync compute below 1x.
"""
import csv
import glob
import os
import sys
from collections import defaultdict
from statistics import median

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
    v = [float(r[col]) for r in g.get(key, [])]
    return median(v) if v else float("nan")


print("| flush | placement | kernel | variant | runs | sync comp s | async comp s | async/sync comp "
      "| async/alone | sync/alone | sync total s | async total s | speedup |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
order = {"shared": 0, "reserved": 1, "iocore": 2}
for flush, place, k, var in sorted({(f, p, k, v) for f, p, k, m, v in g if m == "async"},
                                   key=lambda x: (x[0], x[2], order.get(x[1], 9), x[3])):
    s, a = (flush, place, k, "sync", ""), (flush, place, k, "async", var)
    cs, ca, ts, ta = med(s, "t_comp"), med(a, "t_comp"), med(s, "t_total"), med(a, "t_total")
    print(f"| {flush} | {place} | {k} | {var or '-'} | {len(g.get(s, []))}/{len(g[a])} | {cs:.1f} | {ca:.1f} "
          f"| {ca / cs:.2f}x | {med(a, 'slowdown'):.2f}x | {med(s, 'slowdown'):.2f}x | {ts:.1f} | {ta:.1f} "
          f"| {ts / ta:.2f}x |")
