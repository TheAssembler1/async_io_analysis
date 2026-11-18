#!/usr/bin/env python3
"""PNG figures from the Perlmutter runs.
   plots.py [results/perlmutter] [outdir=figures/perlmutter]     (needs matplotlib)"""
import csv
import glob
import os
import sys
from collections import defaultdict
from statistics import median

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "../../results/perlmutter")
out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(__file__), "../../figures/perlmutter")
os.makedirs(out, exist_ok=True)

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
PCOL = {"shared": "#2a78d6", "smt": "#eb6834", "reserved": "#1baf7a"}
HL, BEST = "#e34948", "#008300"  # same as the laptop grid figures
DEFAULT = (100, 1000)  # PDC's HG_Progress timeout (ms) and sleep (us)
KERNELS = ["sleep", "net", "stream", "spin", "dgemm", "halo"]
KCOL = {"sleep": "#8a8984", "net": "#7a5cc8", "stream": "#1baf7a", "spin": "#2a78d6",
        "dgemm": "#eb6834", "halo": "#c2337a"}
plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "text.color": INK, "font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
    "axes.spines.left": False, "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID,
    "grid.linewidth": 0.8, "axes.axisbelow": True, "legend.frameon": False,
})

# Rows from every PDC job, keyed by (nodes, placement, target, kernel, mode).
rows = defaultdict(list)
for f in glob.glob(os.path.join(src, "pdc_*.csv")):
    if "smoke" in f:
        continue
    for r in csv.DictReader(open(f)):
        _, place, target = r["tag"].split(".")
        rows[(int(r["nranks"]) // 32, place, target, r["kernel"], r["mode"])].append(r)


def med(key, field):
    v = [float(r[field]) for r in rows.get(key, [])]
    return median(v) if v else None


def save(fig, name):
    fig.savefig(os.path.join(out, name), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(os.path.join(out, name))


def slowdown_ticks(ax):
    ax.set_yscale("log")
    ticks = [1, 1.5, 2, 5, 10, 20, 50]
    ax.set_yticks(ticks, [f"{t:g}x" for t in ticks])
    ax.minorticks_off()
    ax.axhline(1, color=INK2, lw=0.8)


# 1: slowdown by kernel and placement, 2 nodes, remote servers
fig, ax = plt.subplots(figsize=(7.5, 3.8))
w = 0.26
for j, p in enumerate(PCOL):
    for i, k in enumerate(KERNELS):
        s = med((2, p, "remote", k, "async"), "slowdown")
        if s is None:
            continue
        x = i + (j - 1) * w
        ax.bar(x, s - 1 if s > 1 else 0.004, bottom=1, width=w - 0.03, color=PCOL[p],
               label=p if i == 0 else None)
        if s >= 1.1:
            ax.text(x, s * 1.04, f"{s:.2f}" if s < 10 else f"{s:.0f}", ha="center",
                    va="bottom", fontsize=7.5, color=INK2)
ax.set_xticks(range(len(KERNELS)), KERNELS)
ax.tick_params(axis="x", length=0)
slowdown_ticks(ax)
ax.set_ylabel("compute slowdown, async vs alone")
ax.set_title("PDC async, 2 nodes, 64 clients writing to the other node's servers", loc="left", fontsize=10)
ax.legend(title="client CPUs", loc="upper left", fontsize=9, title_fontsize=9)
save(fig, "1_slowdown_placement.png")

# 2: slowdown against node count, shared placement
fig, ax = plt.subplots(figsize=(6.5, 3.8))
nodes = [1, 2, 4, 8]
for k in KERNELS:
    ys = [med((n, "shared", "local" if n == 1 else "remote", k, "async"), "slowdown") for n in nodes]
    pts = [(n, y) for n, y in zip(nodes, ys) if y is not None]
    ax.plot(*zip(*pts), color=KCOL[k], lw=2, marker="o", ms=5)
    # spin and dgemm overlap exactly, as do net and sleep at the right edge
    label, dy = {"spin": ("spin, dgemm", 0), "dgemm": (None, 0), "net": ("net", 5), "sleep": ("sleep", -5)}.get(k, (k, 0))
    if label:
        ax.annotate(label, pts[-1], xytext=(6, dy), textcoords="offset points", va="center",
                    fontsize=9, color=INK)
ax.set_xscale("log", base=2)
ax.set_xticks(nodes, [str(n) for n in nodes])
ax.set_xlim(0.85, 11)
ax.set_xlabel("nodes (32 clients + 2 servers each; 1 node writes locally)")
slowdown_ticks(ax)
ax.set_ylabel("compute slowdown, async vs alone")
ax.set_title("PDC async with the progress thread on the client's core", loc="left", fontsize=10)
save(fig, "2_slowdown_nodes.png")

# 3: whole-run speedup of async over sync, 2 nodes remote
fig, ax = plt.subplots(figsize=(7.5, 3.6))
for j, p in enumerate(PCOL):
    for i, k in enumerate(KERNELS):
        a = med((2, p, "remote", k, "async"), "t_total")
        s = med((2, p, "remote", k, "sync"), "t_total")
        if a is None or s is None:
            continue
        x = i + (j - 1) * w
        ax.bar(x, s / a - 1, bottom=1, width=w - 0.03, color=PCOL[p], label=p if i == 0 else None)
        if p == "shared" and s / a < 0.9:
            ax.text(x, s / a - 0.02, f"{s / a:.2f}", ha="center", va="top", fontsize=7.5, color=INK2)
ax.axhline(1, color=INK2, lw=0.8)
ax.set_xticks(range(len(KERNELS)), KERNELS)
ax.tick_params(axis="x", length=0)
ax.set_ylim(0, 1.25)
ax.set_ylabel("sync run time / async run time")
ax.set_title("Above 1, async finishes first (2 nodes, remote servers)", loc="left", fontsize=10, pad=28)
ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), fontsize=9, ncol=3)
save(fig, "3_speedup_placement.png")

# 4: sync I/O bandwidth against node count
fig, ax = plt.subplots(figsize=(5.5, 3.6))
for target, col in (("local", "#8a8984"), ("remote", "#2a78d6")):
    pts = []
    for n in nodes:
        v = [float(r["gb"]) / float(r["t_io_obs"]) for r in rows.get((n, "shared", target, "none", "sync"), [])]
        if v:
            pts.append((n, median(v)))
    if pts:
        ax.plot(*zip(*pts), color=col, lw=2, marker="o", ms=5, label=f"{target} servers")
for n, y in pts:
    ax.annotate(f"{y:.0f}", (n, y), xytext=(-4, 7), textcoords="offset points", ha="right", fontsize=8, color=INK2)
ax.set_xscale("log", base=2)
ax.set_yscale("log", base=2)
ax.set_xticks(nodes, [str(n) for n in nodes])
yt = [4, 8, 16, 32, 64]
ax.set_yticks(yt, [str(t) for t in yt])
ax.minorticks_off()
ax.set_xlabel("nodes")
ax.set_ylabel("GB/s, I/O only, sync")
ax.set_title("PDC write bandwidth, 256 MiB per client per step", loc="left", fontsize=10)
ax.legend(loc="upper left", fontsize=9)
save(fig, "4_bandwidth_nodes.png")

# 5, 6: Mercury-only knobs (knobs*.csv from jobs/knobs.sbatch)
g = defaultdict(list)
for f in sorted(glob.glob(os.path.join(src, "knobs*.csv"))):
    if "smoke" in f:
        continue
    for r in csv.DictReader(open(f)):
        g[(r["placement"], r["kernel"], int(r["blocking"]), int(r["timeout_ms"]), int(r["sleep_us"]))].append(r)
timeouts, sleeps = [0, 1, 10, 100], [0, 100, 1000, 10000]
kerns = [k for k in ("spin", "fma", "stream") if any(key[1] == k for key in g)]
PLACE_DESC = {"shared": "on the same hardware thread as the compute",
              "smt": "free to use the core's second hardware thread",
              "reserved": "free to use a second core"}


def knob_rate(rs):
    """GB/s while the kernel runs: all ranks' bytes per step / forward-to-callback time."""
    v = [float(r["nranks"]) * float(r["mib"]) * (1 << 20) / 1e9 / float(r["t_xfer"]) for r in rs]
    return median(v) if v else None


def knob_slow(rs):
    return median(float(r["slowdown"]) for r in rs) if rs else None


def knobs_grid(place, fname):
    fig, axs = plt.subplots(1, len(kerns), figsize=(3.6 * len(kerns), 3.8), squeeze=False)
    for ax, k in zip(axs[0], kerns):
        m = [[knob_slow(g.get((place, k, 0, t, s))) for s in sleeps] for t in timeouts]
        ax.imshow([[float("nan") if v is None else v for v in row] for row in m],
                  cmap="Blues", vmin=0.9, vmax=2.2, aspect="auto")
        for i, row in enumerate(m):
            for j, v in enumerate(row):
                if v is None:
                    ax.text(j, i, "not run", ha="center", va="center", fontsize=8, color=INK2)
                    continue
                ink = "white" if v > 1.6 else INK
                ax.text(j, i - 0.12, f"{v:.2f}x", ha="center", va="center", fontsize=9.5, color=ink)
                r = knob_rate(g[(place, k, 0, timeouts[i], sleeps[j])])
                ax.text(j, i + 0.2, f"{r:.1f} GB/s", ha="center", va="center", fontsize=7.5, color=ink)
        cells = [(i, j) for i in range(4) for j in range(4) if m[i][j] is not None]
        best = min(cells, key=lambda c: m[c[0]][c[1]])
        di = (timeouts.index(DEFAULT[0]), sleeps.index(DEFAULT[1]))
        for c, col in ((best, BEST), (di, HL)):
            if m[c[0]][c[1]] is not None:
                ax.add_patch(plt.Rectangle((c[1] - 0.45, c[0] - 0.45), 0.9, 0.9, fill=False, lw=3, ec=col))
        ax.set_xticks(range(4), ["0", "100us", "1ms", "10ms"])
        ax.set_yticks(range(4), [f"{t} ms" for t in timeouts])
        ax.set_xlabel("sleep between progress calls")
        ax.grid(False)
        for sp in ax.spines.values():
            sp.set_visible(False)
        b = g.get((place, k, 1, 100, 1000))
        tag = f"; blocking {knob_slow(b):.2f}x, {knob_rate(b):.1f} GB/s" if b else ""
        ax.set_title(f"{k}{tag}", loc="left", fontsize=10)
    axs[0][0].set_ylabel("HG_Progress timeout")
    fig.suptitle(f"Mercury only over ofi+cxi, {place}: progress thread {PLACE_DESC.get(place, place)}",
                 x=0.01, ha="left", fontsize=10)
    fig.tight_layout()
    fig.text(0.01, -0.04, "Large number: compute slowdown. Small: transfer rate while the kernel runs, all 32 clients. "
             "Red: PDC default (timeout 100 ms, sleep 1 ms). Green: lowest slowdown.",
             ha="left", fontsize=9, color=INK2)
    save(fig, fname)


if kerns:
    for place in PCOL:
        if sum((place, k, 0, t, s) in g for k in kerns for t in timeouts for s in sleeps) >= 8:
            knobs_grid(place, "5_knobs_grid.png" if place == "shared" else f"5_knobs_grid_{place}.png")

    # 6: every placement at the same two settings: PDC's default and timeout 0 / sleep 1 ms
    settings = [((0, 100, 1000), "PDC default: timeout 100 ms, sleep 1 ms"),
                ((0, 0, 1000), "timeout 0, sleep 1 ms")]
    bkerns = [k for k in ("sleep", "spin", "fma", "stream") if any(key[1] == k for key in g)]
    fig, axs = plt.subplots(2, 2, figsize=(10, 6), sharey="row", sharex=True)
    for c, (cfg, title) in enumerate(settings):
        for j, p in enumerate(PCOL):
            for i, k in enumerate(bkerns):
                rs = g.get((p, k) + cfg)
                x = i + (j - 1) * w
                if not rs:
                    for ax in axs[:, c]:
                        ax.text(x, 0.5, "not run", rotation=90, ha="center", va="bottom", fontsize=7,
                                color=INK2, transform=ax.get_xaxis_transform())
                    continue
                v, r = knob_slow(rs), knob_rate(rs)
                axs[0, c].bar(x, max(v - 1, 0.004), bottom=1, width=w - 0.03, color=PCOL[p],
                              label=p if i == 0 else None)
                if v >= 1.1:
                    axs[0, c].text(x, v + 0.02, f"{v:.2f}", ha="center", va="bottom", fontsize=7.5, color=INK2)
                axs[1, c].bar(x, r, width=w - 0.03, color=PCOL[p])
                axs[1, c].text(x, r + 0.5, f"{r:.0f}", ha="center", va="bottom", fontsize=7.5, color=INK2)
        axs[0, c].axhline(1, color=INK2, lw=0.8)
        axs[0, c].set_title(title, loc="left", fontsize=10)
    axs[0, 0].set_ylim(0.95, 2.1)
    axs[1, 0].set_ylim(0, 40)
    axs[0, 0].set_ylabel("compute slowdown")
    axs[1, 0].set_ylabel("GB/s while computing")
    for ax in axs[1]:
        ax.set_xticks(range(len(bkerns)), bkerns)
        ax.tick_params(axis="x", length=0)
    axs[0, 0].legend(title="client CPUs", loc="upper left", fontsize=9, title_fontsize=9)
    fig.suptitle("Mercury only over ofi+cxi: progress-thread placement (32 clients, server on the other node)",
                 x=0.01, ha="left", fontsize=10)
    fig.tight_layout()
    save(fig, "6_knobs_placement.png")

# 7: per-step client time, compute stacked under observed I/O; sync and async, 2 nodes, remote servers
COL = {("sync", "comp"): "#d3d2ce", ("sync", "io"): "#9ec3ef",
       ("async", "comp"): "#8a8984", ("async", "io"): "#2a78d6"}
cap = 6.0
PLAB = {"shared": "shared", "smt": "SMT", "reserved": "2 cores"}
MW, GAP = 3.4, 7.6  # x offset of async within a kernel group, and between kernels
fig, ax = plt.subplots(figsize=(14, 4.8))
xt, xl, mt, ml = [], [], [], []
for i, k in enumerate(KERNELS):
    for mi, m in enumerate(["sync", "async"]):
        mt.append(i * GAP + mi * MW + 1)
        ml.append(m)
        for j, p in enumerate(PCOL):
            x = i * GAP + mi * MW + j
            xt.append(x)
            xl.append(PLAB[p])
            key = (2, p, "remote", k, m)
            rs = rows.get(key)
            if not rs:
                continue
            comp = med(key, "t_comp")
            io = median(float(r["t_io_obs"]) / int(r["steps"]) for r in rs)
            c, t = min(comp, cap), min(comp + io, cap)
            first = (i, j) == (0, 0)
            ax.bar(x, c, width=0.8, color=COL[(m, "comp")], label=f"{m} compute" if first else None)
            ax.bar(x, t - c, bottom=c, width=0.8, color=COL[(m, "io")], label=f"{m} I/O" if first else None)
            if comp + io > cap:
                for dy in (-0.25, -0.1):
                    ax.plot([x - 0.45, x + 0.45], [cap + dy - 0.06, cap + dy + 0.06], color=SURFACE, lw=3,
                            solid_capstyle="butt", clip_on=False)
                ax.text(x, cap + 0.08, f"{comp + io:.0f} s", ha="center", va="bottom", fontsize=8,
                        color=INK, fontweight="bold")
            else:
                ax.text(x, t + 0.08, f"{comp + io:.1f}", ha="center", va="bottom", fontsize=7, color=INK2)
# separators on both sides of every kernel group, running down through the labels
for i in range(len(KERNELS) + 1):
    ax.plot([i * GAP - 1.0] * 2, [-0.27, 1.0], color=INK2, lw=1.2, clip_on=False,
            transform=matplotlib.transforms.blended_transform_factory(ax.transData, ax.transAxes))
ax.set_ylim(0, cap + 0.5)
ax.set_yticks(range(0, int(cap) + 1))
ax.set_ylabel("time per step (s)")
ax.set_xlim(-1.2, len(KERNELS) * GAP - 0.8)
ax.set_xticks(xt, xl, fontsize=7.5, color=INK2, rotation=90)
ax.tick_params(axis="x", length=0)
for labels, pos, pad, size, weight in ((ml, mt, 42, 9, "normal"),
                                       (KERNELS, [i * GAP + (MW + 2) / 2 for i in range(len(KERNELS))], 58, 11, "bold")):
    sec = ax.secondary_xaxis("bottom")
    sec.set_xticks(pos, labels, fontsize=size, fontweight=weight, color=INK)
    sec.tick_params(axis="x", length=0, pad=pad)
    sec.spines["bottom"].set_visible(False)
ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), fontsize=9, ncol=4, handlelength=1.2)
ax.set_title("PDC time per step on 2 Perlmutter nodes (64 clients writing to the other node's servers)",
             loc="center", fontsize=11, pad=26)
fig.tight_layout()
fig.text(0.5, -0.01, "Median of runs; each value is the mean over 5 steps. Number above a bar: compute + I/O. "
         "I/O is the time the client spends in the write call and the step's barrier, plus the final wait.",
         ha="center", va="top", fontsize=8.5, color=INK2)
save(fig, "7_step_breakdown.png")
