#!/usr/bin/env python3
"""3D bars and heatmaps over the timeout x sleep grid (tcp, busy, shared).
   plot_grid.py [grid.csv] [outdir]     (needs matplotlib; run_sweep.sh with GRID=1)"""
import csv
import math
import os
import sys
import textwrap
from collections import defaultdict
from statistics import median

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import colors

src = sys.argv[1] if len(sys.argv) > 1 else "grid.csv"
out = sys.argv[2] if len(sys.argv) > 2 else "figures"
os.makedirs(out, exist_ok=True)

SURFACE, INK, INK2 = "#fcfcfb", "#0b0b0b", "#52514e"
plt.rcParams.update({"figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
                     "savefig.facecolor": SURFACE, "text.color": INK, "font.size": 9})
TIMEOUTS = [0, 1, 10, 100]
SLEEPS = [0, 100, 1000, 10000]
SL_LAB = ["0", "0.1", "1", "10"]
KERNELS = ["spin", "fma", "stream"]
GIB_TO_GB = (1 << 30) / 1e9  # rates in GB/s, as in the paper
DEFAULT = (100, 1000)
ELEV, AZIM = 30, -125
HL = "#e34948"    # PDC default bar (red; the value scale is blue)
BEST = "#008300"  # best cell (green)

g = defaultdict(list)
for r in csv.DictReader(open(src)):
    if r["placement"] != "shared" or r["hg"] != "ofi+tcp" or r["blocking"] != "0":
        continue
    g[(int(r["timeout_ms"]), int(r["sleep_us"]), r["kernel"])].append(r)


def med(to, sl, k, col, f=float):
    v = [f(r[col]) for r in g.get((to, sl, k), [])]
    return median(v) if v else None


def excess(to, sl, k):
    s = {r["rep"]: r for r in g.get((to, sl, "sleep"), [])}
    v = []
    for r in g.get((to, sl, k), []):
        sr = s.get(r["rep"])
        if sr:
            pred = float(sr["T_async"]) + float(r["steps"]) * (float(r["t_alone"]) - float(sr["t_alone"]))
            v.append((float(r["T_async"]) / pred - 1) * 100)
    return median(v) if v else None


def grid(fn):
    return [[fn(t, s) for s in SLEEPS] for t in TIMEOUTS]


cmap = matplotlib.colormaps["Blues"]
EXPLAIN = (
    "The client's progress thread loops:  HG_Trigger();  HG_Progress(ctx, timeout);  usleep(sleep).\n"
    "Timeout: the longest one HG_Progress call keeps polling the network when nothing completes. In PDC's busy "
    "mode (NA_NO_BLOCK) it spins on the CPU for that whole time; it returns early once an operation completes.\n"
    "Sleep: how long the thread then sleeps before polling again. While asleep it gives the CPU back, but "
    "nothing drives the transfer. PDC hard-codes timeout 100 ms and sleep 1 ms."
)
nreps = max((len(v) for v in g.values()), default=0)
_r0 = next(iter(g.values()))[0]
STEPS, NR, MIB = int(_r0["steps"]), int(_r0["nranks"]), float(_r0["mib"])
foot = (f"tcp, busy, one hardware thread per rank. {NR} clients x {MIB:.0f} MiB per step, {STEPS} steps per run "
        f"(compute per step = 1.25 x transfer time), median of up to {nreps} runs. "
        "Red: PDC default (timeout 100 ms, sleep 1 ms). Green: best cell. WSL laptop.")


def async_rate(t, s, k):
    """GB/s while the kernel runs: all ranks' bytes / mean forward-to-callback time."""
    v = [float(r["nranks"]) * float(r["mib"]) * (1 << 20) / 1e9 / float(r["t_xfer"])
         for r in g.get((t, s, k), [])]
    return median(v) if v else None


def per_kernel(k):
    return [
        (f"Compute slowdown ({k})", lambda t, s: med(t, s, k, "slowdown"), "{:.2f}x", "lower is better",
         "slowdown, t_comp / t_alone"),
        (f"Run time above sleep prediction ({k})", lambda t, s: excess(t, s, k), "{:+.0f}%", "lower is better",
         "above sleep prediction, %"),
        (f"Transfer rate while {k} runs", lambda t, s: async_rate(t, s, k), "{:.1f}", "higher is better",
         "GB/s, all 4 clients"),
    ]


TRANSFER = [
    ("Transfer rate, rank idle (sync phase)", lambda t, s: med(t, s, "sleep", "gib_s_sync", lambda x: float(x) * GIB_TO_GB),
     "{:.1f}", "higher is better", "GB/s"),
    ("Client CPU per GiB pulled\n(all non-main threads)", lambda t, s: med(t, s, "sleep", "bg_cpu_s_per_gib"),
     "{:.2f}", "lower is better", "CPU s per GiB"),
    ("Thread join after each transfer", lambda t, s: med(t, s, "sleep", "t_join", lambda x: float(x) * 1e3),
     "{:.1f}", "lower is better", "join time, ms"),
]


def depth_order(z):
    """Cells farthest from the camera first (matplotlib's bar3d depth sort is unreliable)."""
    cx, cy = math.cos(math.radians(AZIM)), math.sin(math.radians(AZIM))
    return sorted(((a, b) for a in range(4) for b in range(4) if z[a][b] is not None),
                  key=lambda ab: ab[0] * cx + ab[1] * cy)


def best_cell(z, better):
    pick = max if better.startswith("higher") else min
    return pick(((a, b) for a in range(4) for b in range(4) if z[a][b] is not None),
                key=lambda ab: z[ab[0]][ab[1]])


def finish(fig, fname, bottom):
    width = int(fig.get_figwidth() * 15.5)
    text = "\n".join("\n".join(textwrap.wrap(line, width)) for line in EXPLAIN.split("\n"))
    fig.text(0.01, 0.06, text, fontsize=8.5, color=INK, va="bottom", linespacing=1.4)
    width = int(fig.get_figwidth() * 17)  # characters that fit at 8 pt
    fig.text(0.01, 0.005, "\n".join(textwrap.wrap(foot, width)), fontsize=8, color=INK2, va="bottom")
    fig.tight_layout(rect=(0, bottom, 1, 0.95))
    fig.savefig(os.path.join(out, fname), dpi=170)
    plt.close(fig)
    written.append(fname)


def bars3d(fname, suptitle, panels):
    fig = plt.figure(figsize=(6.2 * len(panels), 7.2))
    for i, (title, fn, fmt, better, zlab) in enumerate(panels):
        ax = fig.add_subplot(1, len(panels), i + 1, projection="3d", computed_zorder=False)
        z = grid(fn)
        vals = [v for row in z for v in row if v is not None]
        if not vals:
            continue
        norm = colors.Normalize(vmin=min(0, min(vals)), vmax=max(vals))
        best = best_cell(z, better)
        for zo, (a, b) in enumerate(depth_order(z)):
            t, s, v = TIMEOUTS[a], SLEEPS[b], z[a][b]
            ax.bar3d(a - 0.3, b - 0.3, min(0, v), 0.6, 0.6, abs(v), shade=True, zorder=zo,
                     color=HL if (t, s) == DEFAULT else BEST if (a, b) == best else cmap(0.25 + 0.7 * norm(v)),
                     edgecolor=INK if (t, s) == DEFAULT else (1, 1, 1, 0.6),
                     linewidth=1.4 if (t, s) == DEFAULT else 0.3)
        ax.set_xticks(range(4), [str(t) for t in TIMEOUTS])
        ax.set_yticks(range(4), SL_LAB)
        ax.set_xlabel("HG_Progress timeout, ms", labelpad=4)
        ax.set_ylabel("sleep after each pass, ms", labelpad=4)
        ax.zaxis.set_rotate_label(False)
        ax.set_zlabel(zlab, labelpad=6, rotation=90)
        ax.set_title(f"{title}\n({better})", fontsize=10, pad=0)
        ax.view_init(elev=ELEV, azim=AZIM)
        ax.tick_params(pad=0, labelsize=8)
        for p in (ax.xaxis.pane, ax.yaxis.pane, ax.zaxis.pane):
            p.set_alpha(0.0)
    fig.suptitle(suptitle, x=0.01, ha="left", fontsize=13)
    finish(fig, fname, 0.17)


def heatmaps(fname, suptitle, panels, sub=None, sub_note=""):
    """sub(t, s) -> GB/s printed under each value (skipped on panels that already show GB/s)."""
    fig, axes = plt.subplots(1, len(panels), figsize=(5.2 * len(panels), 5.6), squeeze=False)
    for ax, (title, fn, fmt, better, zlab) in zip(axes[0], panels):
        z = grid(fn)
        vals = [v for row in z for v in row if v is not None]
        if not vals:
            ax.axis("off")
            continue
        norm = colors.Normalize(vmin=min(vals), vmax=max(vals))
        best = best_cell(z, better)
        for a, t in enumerate(TIMEOUTS):
            for b, s in enumerate(SLEEPS):
                v = z[a][b]
                if v is None:
                    ax.add_patch(plt.Rectangle((b - 0.48, a - 0.48), 0.96, 0.96, color="#efeeeb"))
                    ax.text(b, a, "n/a", ha="center", va="center", color=INK2, fontsize=8)
                    continue
                shade = 0.15 + 0.75 * norm(v)
                ax.add_patch(plt.Rectangle((b - 0.48, a - 0.48), 0.96, 0.96, color=cmap(shade)))
                ink = "white" if shade > 0.55 else INK
                r = sub(t, s) if sub and "GB/s" not in zlab else None
                if r is None:
                    ax.text(b, a, fmt.format(v), ha="center", va="center", fontsize=10, color=ink)
                else:
                    ax.text(b, a + 0.12, fmt.format(v), ha="center", va="center", fontsize=10, color=ink)
                    ax.text(b, a - 0.17, f"{r:.1f} GB/s", ha="center", va="center", fontsize=7.5, color=ink)
                if (t, s) == DEFAULT or (a, b) == best:
                    ax.add_patch(plt.Rectangle((b - 0.45, a - 0.45), 0.9, 0.9, fill=False, lw=3,
                                               ec=HL if (t, s) == DEFAULT else BEST))
        ax.set_xlim(-0.5, 3.5); ax.set_ylim(-0.5, 3.5)
        ax.set_xticks(range(4), SL_LAB); ax.set_yticks(range(4), [str(t) for t in TIMEOUTS])
        ax.set_xlabel("sleep after each pass, ms"); ax.set_ylabel("HG_Progress timeout, ms")
        note = f"\nsmall number: {sub_note}" if sub and "GB/s" not in zlab else ""
        ax.set_title(f"{title}\n({better}; darker = larger){note}", fontsize=10, loc="left")
        for sp in ax.spines.values():
            sp.set_visible(False)
        ax.tick_params(length=0)
    fig.suptitle(suptitle, x=0.01, ha="left", fontsize=13)
    finish(fig, fname, 0.2)


def tradeoff(k):
    """One large 3D figure per kernel: height = slowdown, colour = transfer rate."""
    slow = grid(lambda t, s: med(t, s, k, "slowdown"))
    rate = grid(TRANSFER[0][1])
    rv = [v for row in rate for v in row if v is not None]
    if not rv or not any(v is not None for row in slow for v in row):
        return
    rnorm = colors.Normalize(vmin=min(rv), vmax=max(rv))
    fig = plt.figure(figsize=(11, 9))
    ax = fig.add_subplot(111, projection="3d", computed_zorder=False)
    cells = depth_order(slow)
    best = best_cell(slow, "lower")
    for zo, (a, b) in enumerate(cells):
        t, s, z, r = TIMEOUTS[a], SLEEPS[b], slow[a][b], rate[a][b]
        ax.bar3d(a - 0.27, b - 0.27, 0, 0.54, 0.54, z, shade=True, zorder=zo,
                 color=HL if (t, s) == DEFAULT else BEST if (a, b) == best else
                 (cmap(0.2 + 0.75 * rnorm(r)) if r is not None else "#d9d8d4"),
                 edgecolor=INK if (t, s) == DEFAULT else (1, 1, 1, 0.6),
                 linewidth=1.6 if (t, s) == DEFAULT else 0.3)
    for a, b in cells:
        t, s, z = TIMEOUTS[a], SLEEPS[b], slow[a][b]
        n = len(g.get((t, s, k), []))
        ax.text(a, b, z + 0.05, f"{z:.2f}x" + ("" if n >= 3 else f" (n={n})"), ha="center",
                fontsize=8.5, color=INK, zorder=100,
                bbox=dict(boxstyle="round,pad=0.15", fc=SURFACE, ec="none", alpha=0.85),
                fontweight="bold" if (t, s) == DEFAULT or (a, b) == best else "normal")
    ax.set_xticks(range(4), [str(t) for t in TIMEOUTS])
    ax.set_yticks(range(4), SL_LAB)
    ax.set_xlabel("HG_Progress timeout, ms", labelpad=6)
    ax.set_ylabel("sleep after each pass, ms", labelpad=6)
    ax.zaxis.set_rotate_label(False)
    ax.set_zlabel(f"{k} slowdown, t_comp / t_alone (1.0 = none)", labelpad=8, rotation=90)
    ax.set_zlim(0, max(v for row in slow for v in row if v is not None) * 1.12)
    ax.view_init(elev=ELEV, azim=AZIM)
    for p in (ax.xaxis.pane, ax.yaxis.pane, ax.zaxis.pane):
        p.set_alpha(0.0)
    sm = matplotlib.cm.ScalarMappable(norm=rnorm, cmap=colors.ListedColormap(
        [cmap(0.2 + 0.75 * i / 255) for i in range(256)]))
    cb = fig.colorbar(sm, ax=ax, shrink=0.55, pad=0.08)
    cb.set_label("transfer rate, GB/s (bar colour)")
    cb.outline.set_visible(False)
    di = (TIMEOUTS.index(DEFAULT[0]), SLEEPS.index(DEFAULT[1]))
    fmt_r = lambda a, b: f"{rate[a][b]:.1f} GB/s" if rate[a][b] is not None else "n/a"
    ax.set_title(f"Kernel: {k}. {KDESC[k]}\n"
                 "Bar height: compute slowdown. Bar colour: transfer rate.\n"
                 f"Red: PDC default (timeout 100 ms, sleep 1 ms): {slow[di[0]][di[1]]:.2f}x at {fmt_r(*di)}.\n"
                 f"Green: lowest slowdown (timeout {TIMEOUTS[best[0]]} ms, sleep {SL_LAB[best[1]]} ms): "
                 f"{slow[best[0]][best[1]]:.2f}x at {fmt_r(*best)}. Red and green are not on the colour scale.",
                 fontsize=10, loc="left")
    finish(fig, f"9_grid_3d_tradeoff_{k}.png", 0.15)


def labeled3d(fname, z, fmt, zlab, title, better, kernel_for_n):
    """Large 3D bars with the value printed on every bar."""
    if not any(v is not None for row in z for v in row):
        return
    vals = [v for row in z for v in row if v is not None]
    norm = colors.Normalize(vmin=0, vmax=max(vals))
    best = best_cell(z, better)
    fig = plt.figure(figsize=(11, 9))
    ax = fig.add_subplot(111, projection="3d", computed_zorder=False)
    cells = depth_order(z)
    for zo, (a, b) in enumerate(cells):
        t, s, v = TIMEOUTS[a], SLEEPS[b], z[a][b]
        ax.bar3d(a - 0.27, b - 0.27, 0, 0.54, 0.54, v, shade=True, zorder=zo,
                 color=HL if (t, s) == DEFAULT else BEST if (a, b) == best else cmap(0.25 + 0.7 * norm(v)),
                 edgecolor=INK if (t, s) == DEFAULT else (1, 1, 1, 0.6),
                 linewidth=1.6 if (t, s) == DEFAULT else 0.3)
    top = max(vals)
    for a, b in cells:
        t, s, v = TIMEOUTS[a], SLEEPS[b], z[a][b]
        n = len(g.get((t, s, kernel_for_n), []))
        ax.text(a, b, v + top * 0.03, fmt.format(v) + ("" if n >= 3 else f" (n={n})"), ha="center",
                fontsize=8.5, color=INK, zorder=100,
                bbox=dict(boxstyle="round,pad=0.15", fc=SURFACE, ec="none", alpha=0.85),
                fontweight="bold" if (t, s) == DEFAULT or (a, b) == best else "normal")
    ax.set_xticks(range(4), [str(t) for t in TIMEOUTS])
    ax.set_yticks(range(4), SL_LAB)
    ax.set_xlabel("HG_Progress timeout, ms", labelpad=6)
    ax.set_ylabel("sleep after each pass, ms", labelpad=6)
    ax.zaxis.set_rotate_label(False)
    ax.set_zlabel(zlab, labelpad=8, rotation=90)
    ax.set_zlim(0, top * 1.12)
    ax.view_init(elev=ELEV, azim=AZIM)
    for p in (ax.xaxis.pane, ax.yaxis.pane, ax.zaxis.pane):
        p.set_alpha(0.0)
    di = (TIMEOUTS.index(DEFAULT[0]), SLEEPS.index(DEFAULT[1]))
    if best == di:
        hl = f"Red: PDC default (timeout 100 ms, sleep 1 ms): {fmt.format(z[di[0]][di[1]])}, also the best cell."
    else:
        hl = (f"Red: PDC default (timeout 100 ms, sleep 1 ms): {fmt.format(z[di[0]][di[1]])}.\n"
              f"Green: best ({better}): timeout {TIMEOUTS[best[0]]} ms, sleep {SL_LAB[best[1]]} ms, "
              f"{fmt.format(z[best[0]][best[1]])}.")
    ax.set_title("\n".join(textwrap.wrap(title, 95)) + "\n" + hl, fontsize=10, loc="left")
    finish(fig, fname, 0.15)


KDESC = {
    "spin": "Integer multiply-add loop: CPU only, no floating point or memory traffic.",
    "fma": "Floating-point multiply-add on an L1-resident array: compute-bound, like DGEMM.",
    "stream": "STREAM triad over 3 x 32 MiB arrays: memory-bandwidth-bound.",
}
written = []
for k in KERNELS:
    bars3d(f"7_grid_3d_{k}.png", f"Kernel: {k}. {KDESC[k]}", per_kernel(k))
    heatmaps(f"8_grid_heatmaps_{k}.png", f"Kernel: {k}. {KDESC[k]}", per_kernel(k),
             sub=lambda t, s, k=k: async_rate(t, s, k), sub_note=f"transfer rate while {k} runs")
    tradeoff(k)
    labeled3d(f"10_grid_3d_rate_{k}.png", grid(lambda t, s: async_rate(t, s, k)), "{:.1f} GB/s",
              "transfer rate, GB/s (all 4 clients)",
              f"Transfer rate while {k} runs on the same hardware thread (async phase). {KDESC[k]}",
              "higher is better", k)
labeled3d("10_grid_3d_rate_idle.png", grid(TRANSFER[0][1]), "{:.1f} GB/s", "transfer rate, GB/s (all 4 clients)",
          "Transfer rate with the rank idle (sync phase: transfer, wait, then compute)", "higher is better", "sleep")
bars3d("7_grid_3d_transfer.png", "Transfer cost (sync phase, independent of the compute kernel)", TRANSFER)
heatmaps("8_grid_heatmaps_transfer.png", "Transfer cost (sync phase, independent of the compute kernel)",
         TRANSFER, sub=TRANSFER[0][1], sub_note="transfer rate, rank idle")
print("wrote", ", ".join(written))
