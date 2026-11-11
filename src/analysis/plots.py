#!/usr/bin/env python3
"""Generate the paper's figures.

    python3 src/analysis/plots.py --placeholder      # expected-trend dummy data
    python3 src/analysis/plots.py --csv results.csv  # measured data (TODO)

Placeholder figures carry a "PLACEHOLDER - NOT MEASURED" watermark so they can
never be mistaken for real results.
"""
import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "..", "paper", "figures")

# Validated categorical palette (dataviz reference palette, light mode), in
# fixed order; gray is reserved for baselines/models, not for a series.
C = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
GRAY = "#8a8985"
INK, INK2 = "#0b0b0b", "#52514e"

BACKENDS = ["POSIX AIO", "HDF5 async VOL", "ADIOS2 BP5 async", "PDC"]
SURROGATES = ["sleep", "spin", "DGEMM", "STREAM", "halo", "heat"]
SURR_TICKS = ["sleep", "spin", "DGEMM", "STRM", "halo", "heat"]

plt.rcParams.update({
    "font.family": "serif", "font.size": 7.5, "axes.titlesize": 7.5,
    "axes.labelsize": 7.5, "legend.fontsize": 6.5, "xtick.labelsize": 7,
    "ytick.labelsize": 7, "figure.constrained_layout.use": False, "axes.edgecolor": INK2, "axes.labelcolor": INK,
    "xtick.color": INK2, "ytick.color": INK2, "axes.spines.top": False,
    "axes.spines.right": False, "axes.grid": True, "axes.grid.axis": "y",
    "grid.color": "#e4e3df", "grid.linewidth": 0.5, "axes.axisbelow": True,
    "legend.frameon": False, "hatch.linewidth": 0.6, "savefig.dpi": 300,
})


def watermark(fig, on):
    if on:
        fig.text(0.5, 0.5, "PLACEHOLDER - NOT MEASURED", ha="center",
                 va="center", rotation=20, fontsize=13, color="#e34948",
                 alpha=0.28, weight="bold", zorder=100)


def grouped_bars(ax, groups, series, values, projected=(), width=0.8):
    """values[s][g]; series listed in `projected` are drawn hatched."""
    n = len(series)
    w = width / n
    x = np.arange(len(groups))
    for i, s in enumerate(series):
        hatch = "////" if s in projected else None
        ax.bar(x + (i - (n - 1) / 2) * w, values[s], w * 0.88, color=C[i],
               edgecolor="white" if hatch else C[i], hatch=hatch,
               linewidth=0, label=s + (" (projected)" if hatch else ""))
    ax.set_xticks(x, groups)
    return x


# ---------------------------------------------------------- expected data --
def expected():
    """Expected trends, hand-specified from the mechanisms discussed in the
    paper.  NOT measurements."""
    sp = {  # end-to-end async speedup t_sync / t_async, t_comp ~ 1.25 t_io
        "POSIX AIO":        [1.76, 1.47, 1.41, 1.12, 1.27, 1.16],
        "HDF5 async VOL":   [1.81, 1.36, 1.31, 1.06, 1.17, 1.09],
        "ADIOS2 BP5 async": [1.72, 1.42, 1.36, 1.13, 1.06, 1.10],
        "PDC":              [1.84, 1.66, 1.61, 1.36, 1.46, 1.41],
    }
    slow = {  # compute slowdown t_comp(async) / t_comp(alone)
        "POSIX AIO":        [1.00, 1.18, 1.22, 1.46, 1.31, 1.40],
        "HDF5 async VOL":   [1.00, 1.29, 1.33, 1.58, 1.44, 1.52],
        "ADIOS2 BP5 async": [1.00, 1.21, 1.27, 1.43, 1.62, 1.49],
        "PDC":              [1.00, 1.03, 1.05, 1.21, 1.12, 1.17],
    }
    bw = {  # background write bandwidth achieved during compute, GB/s
        "POSIX AIO":        [2.9, 2.4, 2.3, 1.6, 2.0, 1.8],
        "HDF5 async VOL":   [2.8, 2.1, 2.0, 1.4, 1.8, 1.6],
        "ADIOS2 BP5 async": [2.7, 2.3, 2.2, 1.7, 1.5, 1.6],
        "PDC":              [2.6, 2.5, 2.5, 2.0, 2.3, 2.2],
    }
    return sp, slow, bw


# ---------------------------------------------------------------- figures --
def fig_speedup(sp, ph):
    fig, ax = plt.subplots(figsize=(3.4, 2.0))
    grouped_bars(ax, SURR_TICKS, BACKENDS, sp, projected=("PDC",))
    ax.axhline(1.0, color=INK2, lw=0.8)
    ax.set_ylabel("End-to-end speedup\n(sync / async)")
    ax.set_ylim(0.8, 2.0)
    ax.set_xlabel("Compute surrogate")
    ax.legend(ncol=2, loc="lower center", bbox_to_anchor=(0.5, 1.0),
              handlelength=1.2, columnspacing=0.8)
    watermark(fig, ph)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig_speedup.pdf"))


def fig_breakdown(slow, bw, ph):
    fig, (a, b) = plt.subplots(1, 2, figsize=(7.0, 1.9))
    grouped_bars(a, SURR_TICKS, BACKENDS, slow, projected=("PDC",))
    a.axhline(1.0, color=INK2, lw=0.8)
    a.set_ylim(0.9, 1.75)
    a.set_ylabel("Compute slowdown\n($t_{comp}$ async / alone)")
    a.set_title("(a) Cost paid by the application", loc="left")
    grouped_bars(b, SURR_TICKS, BACKENDS, bw, projected=("PDC",))
    b.axhline(3.0, color=GRAY, lw=0.8, ls="--")
    b.text(5.45, 3.05, "I/O-only", color=INK2, ha="right", va="bottom",
           fontsize=6.5)
    b.set_ylim(0, 3.4)
    b.set_ylabel("Background write\nbandwidth (GB/s)")
    b.set_title("(b) Cost paid by the I/O engine", loc="left")
    h, l = a.get_legend_handles_labels()
    fig.legend(h, l, ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.02))
    watermark(fig, ph)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(os.path.join(OUT, "fig_breakdown.pdf"))


def fig_sweep(ph):
    r = np.linspace(0.25, 3.0, 12)        # t_comp / t_io
    model = (r + 1) / (np.maximum(r, 1) + 0.03)
    # expected: while both are active, compute runs 1/s and I/O 1/k as fast
    # as alone; whichever finishes first frees the other to run at full rate.
    def contended(s, k):
        t1 = np.minimum(r * s, k)                         # both active
        epoch = np.where(r * s <= k, t1 + 1 - t1 / k,     # compute first
                         t1 + r - t1 / s)                 # I/O first
        return (r + 1) / (epoch + 0.06)
    sleep = contended(1.0, 1.0)
    dgemm = contended(1.30, 1.25)
    stream = contended(1.55, 1.60)
    fig, ax = plt.subplots(figsize=(3.4, 2.0))
    ax.plot(r, model, color=GRAY, lw=1.2, ls="--", label="model (Ravi et al.)")
    for y, lab, col, mk in [(sleep, "sleep", C[0], "o"),
                            (dgemm, "DGEMM", C[1], "s"),
                            (stream, "STREAM", C[2], "^")]:
        ax.plot(r, y, color=col, lw=1.6, marker=mk, ms=3.5, label=lab,
                markeredgecolor="white", markeredgewidth=0.5)
    ax.axhline(1.0, color=INK2, lw=0.8)
    ax.axvline(1.0, color="#e4e3df", lw=0.8)
    ax.set_xlabel("$t_{comp}/t_{io}$ (standalone)")
    ax.set_ylabel("End-to-end speedup")
    ax.set_ylim(0.8, 2.1)
    ax.legend(loc="lower right", ncol=2)
    watermark(fig, ph)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig_sweep.pdf"))


def fig_cores_gpu(ph):
    fig, (a, b) = plt.subplots(1, 2, figsize=(7.0, 1.9))
    alloc = ["fully subscribed", "SMT sibling free", "core reserved"]
    vals = {"sleep": [1.80, 1.81, 1.79], "DGEMM": [1.31, 1.52, 1.66],
            "STREAM": [1.06, 1.14, 1.27]}
    grouped_bars(a, alloc, list(vals), vals)
    a.axhline(1.0, color=INK2, lw=0.8)
    a.set_ylim(0.8, 2.2)
    a.set_ylabel("End-to-end speedup")
    a.set_title("(a) Core allocation (HDF5 async VOL)", loc="left")
    a.legend(ncol=3, loc="upper left")
    comp = ["sleep", "GPU,\nhost blocks", "GPU,\nhost spins", "CPU\nDGEMM"]
    vals2 = {"HDF5 async VOL": [1.81, 1.74, 1.49, 1.31],
             "ADIOS2 BP5 async": [1.72, 1.66, 1.45, 1.36]}
    x = np.arange(len(comp))
    for i, (s, v) in enumerate(vals2.items()):
        b.bar(x + (i - 0.5) * 0.36, v, 0.32, color=C[i + 1], label=s)
    b.set_xticks(x, comp)
    b.axhline(1.0, color=INK2, lw=0.8)
    b.set_ylim(0.8, 2.2)
    b.set_title("(b) CPU vs. GPU-resident computation", loc="left")
    b.legend(ncol=2, loc="upper right")
    watermark(fig, ph)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "fig_cores_gpu.pdf"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--placeholder", action="store_true")
    ap.add_argument("--csv")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if not args.placeholder:
        raise SystemExit("measured-data path not implemented yet; use --placeholder")
    sp, slow, bw = expected()
    fig_speedup(sp, True)
    fig_breakdown(slow, bw, True)
    fig_sweep(True)
    fig_cores_gpu(True)
    print("wrote placeholder figures to", os.path.normpath(OUT))


if __name__ == "__main__":
    main()
