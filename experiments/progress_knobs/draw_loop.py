#!/usr/bin/env python3
"""Flow diagram of a PDC async write: the application thread and the
client progress thread, and where the two knobs (TIMEOUT, SLEEP) act.
   draw_loop.py [outdir]     (needs matplotlib)"""
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

out = sys.argv[1] if len(sys.argv) > 1 else "figures"
os.makedirs(out, exist_ok=True)

SURFACE, INK, INK2, LANE = "#fcfcfb", "#0b0b0b", "#52514e", "#f1f0ec"
BOX, DEC = "#ffffff", "#eef4fc"

fig, ax = plt.subplots(figsize=(11, 10.2))
fig.patch.set_facecolor(SURFACE)
ax.set_xlim(0, 11)
ax.set_ylim(0, 9.85)
ax.axis("off")

for x0, x1, title, sub in [(0.2, 5.2, "Application thread", "the rank's compute"),
                           (5.6, 10.8, "PDC progress thread", "hg_progress_fn in pdc_client_connect.c")]:
    ax.add_patch(FancyBboxPatch((x0, 1.0), x1 - x0, 8.75, boxstyle="round,pad=0,rounding_size=0.15",
                                fc=LANE, ec="none", zorder=0))
    ax.text((x0 + x1) / 2, 9.45, title, ha="center", va="center", fontsize=13, fontweight="bold")
    ax.text((x0 + x1) / 2, 9.12, sub, ha="center", va="center", fontsize=9, color=INK2)

nodes = {}


def box(key, x, y, w, h, text, kind="box", fs=9.5):
    fc = {"box": BOX, "dec": DEC, "knob": BOX, "end": "#e8e7e3"}[kind]
    style = "round,pad=0.02,rounding_size=0.25" if kind == "dec" else "round,pad=0.02,rounding_size=0.08"
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h, boxstyle=style, fc=fc,
                                ec=INK2, lw=1.0, zorder=2))
    ax.text(x, y, text, ha="center", va="center", fontsize=fs, zorder=3, linespacing=1.3)
    nodes[key] = (x, y, w, h)


def P(key, side):
    x, y, w, h = nodes[key]
    return {"t": (x, y + h / 2), "b": (x, y - h / 2), "l": (x - w / 2, y), "r": (x + w / 2, y)}[side]


def path(points, label=None, lxy=None):
    """Arrow through a list of points; the head is on the last segment."""
    for i in range(len(points) - 1):
        last = i == len(points) - 2
        ax.add_patch(FancyArrowPatch(points[i], points[i + 1], lw=1.3, color=INK,
                                     arrowstyle="-|>" if last else "-", mutation_scale=14, zorder=1,
                                     shrinkA=0, shrinkB=1 if last else 0))
    if label:
        ax.text(*lxy, label, fontsize=9, ha="center", va="center", zorder=4,
                bbox=dict(boxstyle="round,pad=0.15", fc=SURFACE, ec="none"))


# ---- application thread
A = 2.7
box("a1", A, 8.4, 4.4, 0.55, "PDCregion_transfer_start()")
box("a2", A, 7.45, 4.4, 0.85, "start the progress thread\n(no affinity: it inherits the rank's\nCPU binding and shares its core)")
box("a3", A, 6.35, 4.4, 0.65, "send a small request to the server\n(the server then pulls the data)")
box("a4", A, 5.15, 4.4, 1.0, "COMPUTE\nruns on the same hardware thread as\nthe progress thread, so the OS\ntime-slices the two")
box("a5", A, 3.85, 4.4, 0.75, "PDCregion_transfer_wait():\nwrite done?", "dec")
box("a6", 1.05, 2.85, 1.3, 0.48, "usleep(1 ms)", fs=9)
box("a7", A, 1.9, 4.4, 1.0, "PDCregion_transfer_close():\nset the stop flag, pthread_join()\nwaits until the thread notices it,\nup to one full TIMEOUT", "knob")
path([P("a1", "b"), P("a2", "t")])
path([P("a2", "b"), P("a3", "t")])
path([P("a3", "b"), P("a4", "t")])
path([P("a4", "b"), P("a5", "t")])
path([P("a5", "l"), (1.05, P("a5", "l")[1]), P("a6", "t")], "no", (0.75, 3.15))
path([P("a6", "r"), (A - 0.85, P("a6", "r")[1]), (A - 0.85, P("a5", "b")[1])])
path([(A + 0.6, P("a5", "b")[1]), (A + 0.6, P("a7", "t")[1])], "yes", (A + 0.95, 2.95))

# ---- progress thread
B = 8.2
box("b1", B, 8.4, 3.0, 0.65, "stop flag set?\n(set by transfer_close)", "dec", fs=9)
box("b0", 10.25, 8.4, 0.75, 0.45, "exit", "end", fs=9)
box("b2", B, 7.15, 4.4, 0.95, "HG_Trigger(): run completion callbacks,\ne.g. mark the write done\n(what transfer_wait checks)")
box("b3", B, 5.45, 4.4, 1.6, "HG_Progress(ctx, TIMEOUT)\nTIMEOUT = 100 ms in PDC\n\npolls the network; in PDC's default busy\nmode it SPINS until something completes\nor TIMEOUT runs out. Data only moves\ninside this call.", "knob", fs=9.5)
box("b4", B, 3.45, 4.4, 1.15, "usleep(SLEEP)\nSLEEP = 1 ms in PDC\n\ngives the CPU back, but nothing\nmoves data meanwhile", "knob", fs=9.5)
path([P("b1", "b"), P("b2", "t")], "no", (B + 0.3, 7.83))
path([P("b1", "r"), P("b0", "l")], "yes", (9.82, 8.72))
path([P("b2", "b"), P("b3", "t")])
path([P("b3", "b"), P("b4", "t")], "returns", (B + 0.62, 4.42))
path([P("b4", "l"), (5.8, P("b4", "l")[1]), (5.8, P("b1", "l")[1]), P("b1", "l")], "loop", (5.8, 6.3))
ax.text(B, 2.5, "The thread lives from transfer_start to transfer_close,\nso it competes with COMPUTE for the whole compute\nphase, not only while bytes are moving.",
        ha="center", va="top", fontsize=9, color=INK2, linespacing=1.35)

# ---- key
ax.add_patch(FancyBboxPatch((0.4, 0.33), 0.5, 0.3, boxstyle="round,pad=0,rounding_size=0.08", fc=DEC, ec=INK2))
ax.text(1.0, 0.48, "decision", va="center", fontsize=9.5)

fig.suptitle("One asynchronous PDC write: where TIMEOUT and SLEEP act", x=0.02, y=0.975, ha="left",
             fontsize=15, fontweight="bold")
fig.tight_layout(rect=(0, 0, 1, 0.955))
for ext in ("png", "svg", "pdf"):
    fig.savefig(os.path.join(out, f"0_progress_loop_flow.{ext}"), dpi=180)
print("wrote 0_progress_loop_flow.{png,svg,pdf}")
