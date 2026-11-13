#!/usr/bin/env python3
"""PNG figures from run_sweep.sh output.
   plots.py [results.csv] [outdir]     (needs matplotlib)"""
import csv
import os
import sys
from collections import defaultdict
from statistics import median

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

src = sys.argv[1] if len(sys.argv) > 1 else "results.csv"
out = sys.argv[2] if len(sys.argv) > 2 else "figures"
os.makedirs(out, exist_ok=True)

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
KCOL = {"spin": "#2a78d6", "fma": "#eb6834", "stream": "#1baf7a"}
ONE = "#2a78d6"
KERNELS = ["spin", "fma", "stream"]
GIB_TO_GB = (1 << 30) / 1e9  # rates in GB/s, as in the paper
plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK,
    "text.color": INK, "font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
    "axes.spines.left": False, "axes.grid": True, "axes.grid.axis": "x", "grid.color": GRID,
    "grid.linewidth": 0.8, "axes.axisbelow": True,
})

groups = defaultdict(list)
for r in csv.DictReader(open(src)):
    cfg = (r["placement"], r["hg"].replace("ofi+", ""), int(r["blocking"]), int(r["timeout_ms"]),
           int(r["sleep_us"]))
    groups[cfg + (r["kernel"],)].append(r)

order = {"shared": 0, "smt": 1}
cfgs = sorted({k[:5] for k in groups},
              key=lambda c: (order.get(c[0], 9), c[1] != "tcp", c[1], c[2], -c[3], c[4]))
DEFAULT = ("shared", "tcp", 0, 100, 1000)


def label(c):
    pl, hg, blk, to, sl = c
    sl_s = f"{sl // 1000} ms" if sl >= 1000 else f"{sl} us"
    s = f"{hg}, {'blocking' if blk else 'busy'}, timeout {to} ms, sleep {sl_s}"
    if pl == "smt":
        s = "SMT: " + s
    if c == DEFAULT:
        s += "  (PDC default)"
    return s


def vals(key, col):
    return [float(r[col]) for r in groups.get(key, [])]


def stat(key, col, f=lambda x: x):
    v = [f(x) for x in vals(key, col)]
    return (median(v), min(v), max(v)) if v else (float("nan"),) * 3


def excess_runs(c, k):
    """Per-rep run time vs. the sleep prediction (same config, same rep)."""
    s = {r["rep"]: r for r in groups.get(c + ("sleep",), [])}
    res = []
    for r in groups.get(c + (k,), []):
        sr = s.get(r["rep"])
        if not sr:
            continue
        pred = float(sr["T_async"]) + float(r["steps"]) * (float(r["t_alone"]) - float(sr["t_alone"]))
        res.append((float(r["T_async"]) / pred - 1) * 100)
    return res


def style_labels(ax):
    for t in ax.get_yticklabels():
        if "PDC default" in t.get_text():
            t.set_fontweight("bold")


def grouped(fname, title, xlabel, getter, ref=None, ref_label=None):
    n = len(cfgs)
    fig, ax = plt.subplots(figsize=(10, 0.55 * n + 1.6))
    h = 0.26
    for j, k in enumerate(KERNELS):
        ys = [i + (j - 1) * h for i in range(n)]
        md, lo, hi = zip(*[getter(c, k) for c in cfgs])
        ax.barh(ys, md, height=h - 0.04, color=KCOL[k], label=k, zorder=2)
        ax.errorbar(md, ys, xerr=[[m - l for m, l in zip(md, lo)], [u - m for m, u in zip(md, hi)]],
                    fmt="none", ecolor=INK2, elinewidth=0.8, capsize=0, zorder=3)
    if ref is not None:
        ax.axvline(ref, color=INK2, lw=1, ls="--", zorder=1)
        if ref_label:
            ax.annotate(ref_label, (ref, -0.75), xytext=(4, 0), textcoords="offset points",
                        color=INK2, fontsize=9, ha="left", va="bottom")
    ax.set_yticks(range(n))
    ax.set_yticklabels([label(c) for c in cfgs])
    ax.invert_yaxis()
    ax.tick_params(axis="y", length=0)
    style_labels(ax)
    ax.set_xlabel(xlabel)
    ax.set_title(title, loc="left", fontsize=12, color=INK, pad=24)
    ax.legend(ncol=3, loc="lower left", bbox_to_anchor=(0, 1.0), frameon=False, fontsize=9,
              handlelength=1.2, borderaxespad=0.2)
    fig.text(0.01, 0.005, "Bars: median of 3 runs; whiskers: min-max. 4 clients x 256 MiB per step, 5 steps per run, "
             "compute = 1.25 x transfer time. WSL laptop.", fontsize=8, color=INK2)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(os.path.join(out, fname), dpi=200)
    plt.close(fig)


def single(fname, title, xlabel, getter, fmt, ref=None, ref_label=None):
    n = len(cfgs)
    fig, ax = plt.subplots(figsize=(10, 0.42 * n + 1.4))
    md, lo, hi = zip(*[getter(c) for c in cfgs])
    ax.barh(range(n), md, height=0.62, color=ONE, zorder=2)
    ax.errorbar(md, range(n), xerr=[[m - l for m, l in zip(md, lo)], [u - m for m, u in zip(md, hi)]],
                fmt="none", ecolor=INK2, elinewidth=0.8, capsize=0, zorder=3)
    for i, (m, u) in enumerate(zip(md, hi)):
        ax.text(u + max(hi) * 0.01, i, fmt(m), va="center", fontsize=8.5, color=INK2)
    if ref is not None:
        ax.axvline(ref, color=INK2, lw=1, ls="--", zorder=1)
        if ref_label:
            ax.annotate(ref_label, (ref, -0.75), xytext=(4, 0), textcoords="offset points",
                        color=INK2, fontsize=9, ha="left", va="bottom")
    ax.set_yticks(range(n))
    ax.set_yticklabels([label(c) for c in cfgs])
    ax.invert_yaxis()
    ax.tick_params(axis="y", length=0)
    style_labels(ax)
    ax.set_xlim(0, max(hi) * 1.12)
    ax.set_xlabel(xlabel)
    ax.set_title(title, loc="left", fontsize=12, color=INK, pad=18)
    fig.text(0.01, 0.005, "Median of 3 runs; whiskers: min-max. 4 clients x 256 MiB per step, 5 steps per run. WSL laptop.",
             fontsize=8, color=INK2)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(os.path.join(out, fname), dpi=200)
    plt.close(fig)


# 1. compute slowdown
grouped("1_compute_slowdown.png", "Compute slowdown while a transfer is in flight",
        "t_comp / t_alone (1.0 = no interference)",
        lambda c, k: stat(c + (k,), "slowdown"), ref=1.0, ref_label="no interference")

# 2. run time vs sleep prediction
def ex(c, k):
    v = excess_runs(c, k)
    return (median(v), min(v), max(v)) if v else (float("nan"),) * 3


grouped("2_vs_sleep_prediction.png", "Async run time vs. what a sleep() benchmark predicts",
        "run time above the sleep prediction (%)", ex, ref=0, ref_label="sleep prediction")

# 3. client CPU per GiB moved
single("3_client_cpu_per_gib.png", "Client CPU spent per GiB the server pulls",
       "CPU seconds of all non-main client threads per GiB (sync phase)",
       lambda c: stat(c + ("sleep",), "bg_cpu_s_per_gib"), lambda m: f"{m:.2f} s")

# 4. transfer rate
single("4_transfer_rate.png", "Transfer rate with the rank idle (sync phase)", "GB/s, all 4 clients together",
       lambda c: stat(c + ("sleep",), "gib_s_sync", lambda x: x * GIB_TO_GB), lambda m: f"{m:.1f}")

# 4b. transfer rate while computing, per kernel
def arate(c, k):
    v = [float(r["nranks"]) * float(r["mib"]) * (1 << 20) / 1e9 / float(r["t_xfer"])
         for r in groups.get(c + (k,), [])]
    return (median(v), min(v), max(v)) if v else (float("nan"),) * 3


grouped("4b_transfer_rate_while_computing.png", "Transfer rate while the kernel runs (async phase)",
        "GB/s, all 4 clients together (higher is better)", arate)

# 5. join latency
single("5_join_latency.png", "Time to stop the progress thread after each transfer",
       "ms per transfer (PDCregion_transfer_close in PDC)",
       lambda c: stat(c + ("sleep",), "t_join", lambda x: x * 1e3), lambda m: f"{m:.1f} ms")

# 6. trade-off: transfer rate vs. interference
fig, ax = plt.subplots(figsize=(9, 6))
ax.grid(True, axis="both")
pts = []
for c in cfgs:
    x = stat(c + ("sleep",), "gib_s_sync", lambda x: x * GIB_TO_GB)[0]
    y = median([stat(c + (k,), "slowdown")[0] for k in KERNELS])
    pts.append((c, x, y))
    ax.scatter([x], [y], s=60, color=ONE, edgecolors=SURFACE, linewidths=2, zorder=3,
               marker="D" if c[0] == "smt" else "o")
short = lambda c: (("SMT " if c[0] == "smt" else "") + f"{c[1]} {'blk' if c[2] else 'busy'} "
                   f"{c[3]}/{c[4] // 1000 if c[4] >= 1000 else c[4] / 1000:g}")
# hand-placed labels for the crowded top-right cluster (offsets in points)
nudge = {DEFAULT: (-30, 30, "right"), ("shared", "tcp", 1, 100, 1000): (0, -16, "center"),
         ("shared", "tcp", 0, 100, 0): (-10, 12, "right")}
for c, x, y in pts:
    dx, dy, ha = nudge.get(c, (6, 4, "left"))
    arrow = dict(arrowstyle="-", color=INK2, lw=0.6, shrinkA=0, shrinkB=4) if c in nudge else None
    ax.annotate(short(c) + ("  (PDC default)" if c == DEFAULT else ""), (x, y), xytext=(dx, dy),
                textcoords="offset points", fontsize=8, color=INK, ha=ha, arrowprops=arrow,
                fontweight="bold" if c == DEFAULT else "normal")
ax.set_ylim(0.95, 2.05)
ax.axhline(1.0, color=INK2, lw=1, ls="--", zorder=1)
ax.set_xlabel("transfer rate, GB/s (higher is better)")
ax.set_ylabel("compute slowdown, median of spin/fma/stream (lower is better)")
ax.set_title("Trade-off: transfer rate vs. interference", loc="left", fontsize=12, pad=12)
ax.spines["left"].set_visible(True)
fig.text(0.01, 0.005, "Labels: transport, mode, timeout ms / sleep ms. Circles: one hardware thread per "
         "rank; diamonds: SMT sibling free. Median of 3 runs.", fontsize=8, color=INK2)
fig.tight_layout(rect=(0, 0.03, 1, 1))
fig.savefig(os.path.join(out, "6_tradeoff.png"), dpi=200)
plt.close(fig)
print("wrote", ", ".join(sorted(os.listdir(out))))
