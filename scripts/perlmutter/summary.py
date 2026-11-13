#!/usr/bin/env python3
"""Medians over repetitions from the aio_bench CSVs the PDC jobs write.
   summary.py [csv ...]      default: results/perlmutter/pdc_*.csv

One row per nodes x config x kernel: sync I/O-only rate, compute slowdown
under async I/O, async I/O time left per step, and async speedup over sync."""
import csv
import glob
import os
import sys
from collections import defaultdict
from statistics import median

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "results", "perlmutter")
files = sys.argv[1:] or sorted(f for f in glob.glob(os.path.join(root, "pdc_*.csv")) if "smoke" not in f)
CPN = int(os.environ.get("CPN", 32))

g = defaultdict(list)
for f in files:
    for r in csv.DictReader(open(f)):
        nodes = int(r["nranks"]) // CPN
        g[(nodes, r["tag"], r["kernel"], r["mode"])].append(r)


def med(key, col):
    v = [float(r[col]) for r in g.get(key, [])]
    return median(v) if v else float("nan")


print("| nodes | transport.placement.target | kernel | sync I/O GB/s | slowdown (async) "
      "| async I/O s/step | sync s | async s | speedup | runs |")
print("|---|---|---|---|---|---|---|---|---|---|")
for nodes, tag, k in sorted({key[:3] for key in g if key[2] != "none"}):
    base = (nodes, tag, "none", "sync")
    gbs = med(base, "gb") / med(base, "steps") / med(base, "t_io_step") if base in g else float("nan")
    s, a = (nodes, tag, k, "sync"), (nodes, tag, k, "async")
    ts, ta = med(s, "t_total"), med(a, "t_total")
    print(f"| {nodes} | {tag} | {k} | {gbs:.1f} | {med(a, 'slowdown'):.2f}x | {med(a, 't_io_step'):.3f} "
          f"| {ts:.1f} | {ta:.1f} | {ts / ta:.2f}x | {len(g.get(a, []))} |")
