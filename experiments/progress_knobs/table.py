#!/usr/bin/env python3
"""Markdown tables from run_sweep.sh output (medians over reps).
   table.py [results.csv] > results.md"""
import csv
import sys
from collections import defaultdict
from statistics import median

rows = list(csv.DictReader(open(sys.argv[1] if len(sys.argv) > 1 else "results.csv")))
groups = defaultdict(list)
for r in rows:
    cfg = (r["placement"], r["hg"].replace("ofi+", ""), int(r["blocking"]), int(r["timeout_ms"]),
           int(r["sleep_us"]))
    groups[cfg + (r["kernel"],)].append(r)


def med(key, col):
    g = groups.get(key)
    return median(float(r[col]) for r in g) if g else float("nan")


def excess(cfg, k):
    """Async run time vs. what the sleep run of the same config predicts."""
    s, x = cfg + ("sleep",), cfg + (k,)
    if s not in groups or x not in groups:
        return float("nan")
    steps = float(groups[x][0]["steps"])
    pred = med(s, "T_async") + steps * (med(x, "t_alone") - med(s, "t_alone"))
    return (med(x, "T_async") / pred - 1) * 100


order = {"shared": 0, "smt": 1}
cfgs = sorted({k[:5] for k in groups}, key=lambda c: (order.get(c[0], 9), c[1] != "tcp", c[1], c[2], -c[3], c[4]))
kernels = [k for k in ("spin", "fma", "stream") if any(g[5] == k for g in groups)]
cols = "| placement | transport | mode | timeout ms | sleep us |"
sep = "|---|---|---|---|---|"
label = lambda c: f"| {c[0]} | {c[1]} | {'blocking' if c[2] else 'busy'} | {c[3]} | {c[4]} |"
pct = lambda x: f"{x * 100:.0f}%"

print("### Transfer cost (sync phase, nothing else running on the rank)\n")
print(cols + " transfer s/step | GB/s (all ranks) | thread join ms | client bg CPU during transfer | client bg CPU s/GiB |")
print(sep + "---|---|---|---|---|")
for c in cfgs:
    k = c + ("sleep",)
    print(label(c) + f" {med(k, 't_xfer_sync'):.3f} | {med(k, 'gib_s_sync') * (1 << 30) / 1e9:.1f} | {med(k, 't_join') * 1e3:.1f} "
          f"| {pct(med(k, 'bg_cpu_xfer'))} | {med(k, 'bg_cpu_s_per_gib'):.2f} |")

print("\n### Client background CPU over the async run (sleep kernel)\n")
print(cols + " progress thread | all non-main threads | threads |")
print(sep + "---|---|---|")
for c in cfgs:
    k = c + ("sleep",)
    print(label(c) + f" {pct(med(k, 'pt_cpu_async'))} | {pct(med(k, 'bg_cpu_async'))} | {med(k, 'nthreads'):.0f} |")

print("\n### Transfer rate while the kernel runs (async phase), GB/s\n")
print(cols + " " + " | ".join(kernels) + " |")
print(sep + "---|" * len(kernels))
for c in cfgs:
    print(label(c) + " " + " | ".join(
        f"{median(float(r['nranks']) * float(r['mib']) * (1 << 20) / 1e9 / float(r['t_xfer']) for r in groups[c + (k,)]):.1f}"
        if c + (k,) in groups else "n/a" for k in kernels) + " |")

print("\n### Compute slowdown (t_comp / t_alone)\n")
print(cols + " " + " | ".join(kernels) + " |")
print(sep + "---|" * len(kernels))
for c in cfgs:
    print(label(c) + " " + " | ".join(f"{med(c + (k,), 'slowdown'):.2f}x" for k in kernels) + " |")

print("\n### Exposed time per step after compute (wait + join), ms\n")
print(cols + " sleep | " + " | ".join(kernels) + " |")
print(sep + "---|" * (len(kernels) + 1))
for c in cfgs:
    print(label(c) + " " + " | ".join(
        f"{(med(c + (k,), 't_wait') + med(c + (k,), 't_join')) * 1e3:.0f}" for k in ["sleep"] + kernels) + " |")

print("\n### Run time vs. sleep prediction of the same config\n")
print(cols + " " + " | ".join(kernels) + " |")
print(sep + "---|" * len(kernels))
for c in cfgs:
    print(label(c) + " " + " | ".join(f"{excess(c, k):+.0f}%" for k in kernels) + " |")

n = max(len(g) for g in groups.values())
r0 = rows[0]
print(f"\n{r0['nranks']} clients x {float(r0['mib']):.0f} MiB per step, {r0['steps']} steps, median of "
      f"{n} runs. Compute per step = 1.25 x transfer time. Server busy-polls on its own core.")
