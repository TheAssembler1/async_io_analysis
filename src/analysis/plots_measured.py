#!/usr/bin/env python3
"""Figures, tables and LaTeX macros from measured results (results/*.csv).

    python3 src/analysis/plots_measured.py

Writes paper/figures/m_*.pdf and paper/results_macros.tex.  Every number
quoted in the paper's results section should come from the macros file.

Metrics (per configuration; sync and async rows share io, kernel, binding,
ranks, size, steps and t_comp):
  speedup    S = T_sync / T_async                (end-to-end, all steps)
  slowdown   t_comp(async) / t_comp(sync)        (compute phase; also vs. alone)
  exposed    observed async I/O per step / sync I/O per step
  overlap    (t_io + t_comp - t_epoch) / min(t_io, t_comp)   (IMB-IO)
"""
import csv
import glob
import os
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
RES = os.path.join(ROOT, "results")
OUT = os.path.join(ROOT, "paper", "figures")
MACROS = os.path.join(ROOT, "paper", "results_macros.tex")

C = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
GRAY, INK, INK2 = "#8a8985", "#0b0b0b", "#52514e"
BACKENDS = ["posix", "mpiio", "hdf5", "adios2"]
BNAME = {"posix": "POSIX AIO", "hdf5": "HDF5 async VOL", "adios2": "ADIOS2 BP5 async",
         "mpiio": "MPI-IO iwrite", "pdc": "PDC"}
KERNELS = ["sleep", "spin", "dgemm", "stream", "halo", "heat"]
KNAME = {"sleep": "sleep", "spin": "spin", "dgemm": "DGEMM", "stream": "STREAM",
         "halo": "halo", "heat": "heat", "gpu-block": "GPU,\nhost blocks",
         "gpu-spin": "GPU,\nhost spins", "none": "none"}

plt.rcParams.update({
    "font.family": "sans-serif", "font.sans-serif": ["TeX Gyre Heros", "DejaVu Sans"],
    "mathtext.fontset": "custom", "mathtext.rm": "TeX Gyre Heros",
    "mathtext.it": "TeX Gyre Heros:italic", "mathtext.bf": "TeX Gyre Heros:bold",
    "font.size": 7.5, "axes.titlesize": 7.5,
    "axes.labelsize": 7.5, "legend.fontsize": 6.5, "xtick.labelsize": 7,
    "ytick.labelsize": 7, "axes.edgecolor": INK2, "axes.labelcolor": INK,
    "xtick.color": INK2, "ytick.color": INK2, "axes.spines.top": False,
    "axes.spines.right": False, "axes.grid": True, "axes.grid.axis": "y",
    "grid.color": "#e4e3df", "grid.linewidth": 0.5, "axes.axisbelow": True,
    "legend.frameon": False, "savefig.dpi": 300,
})


# ------------------------------------------------------------------ data --
def load(phase):
    path = os.path.join(RES, f"{phase}.csv")
    if not os.path.exists(path):
        return []
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            for k in ("nranks", "steps", "durable"):
                r[k] = int(r[k])
            for k in ("mib_per_rank", "tcomp_target", "units", "t_alone", "t_comp", "slowdown",
                      "t_io_step", "t_final", "t_io_obs", "t_total", "t_init", "gb"):
                r[k] = float(r[k])
            r["tc"] = round(r["tcomp_target"], 2)  # strip the repetition offset
            rows.append(r)
    return rows


def pairs(rows):
    """Group sync/async rows of the same configuration; returns dict
    key -> {"sync": [rows], "async": [rows]}."""
    g = defaultdict(lambda: {"sync": [], "async": []})
    for r in rows:
        key = (r["io"], r["kernel"], r["tag"], r["nranks"], r["mib_per_rank"], r["steps"], r["tc"])
        g[key][r["mode"]].append(r)
    return {k: v for k, v in g.items() if v["sync"] and v["async"]}


def mean(xs):
    return float(np.mean(xs)) if xs else float("nan")


def metrics(p, t_io_sync=None):
    ts = [r["t_total"] for r in p["sync"]]
    ta = [r["t_total"] for r in p["async"]]
    a = p["async"]
    steps = a[0]["steps"]
    m = {
        "S": mean(ts) / mean(ta),
        "S_lo": min(ts) / max(ta) if len(ts) > 1 else None,
        "S_hi": max(ts) / min(ta) if len(ts) > 1 else None,
        # primary: async compute phase vs. the same configuration's sync
        # compute phase (no background I/O during compute, same CPU frequency
        # regime); secondary: vs. the standalone calibration baseline
        "slow": (mean([r["t_comp"] for r in a]) / mean([r["t_comp"] for r in p["sync"]])
                 if a[0]["t_alone"] > 0 else 1.0),
        "slow_alone": mean([r["slowdown"] for r in a]) if a[0]["t_alone"] > 0 else 1.0,
        "T_sync": mean(ts), "T_async": mean(ta),
        "io_obs_async": mean([r["t_io_obs"] for r in a]) / steps,
        "t_alone": mean([r["t_alone"] for r in a]),
        "n": min(len(ts), len(ta)),
    }
    if t_io_sync:
        m["exposed"] = m["io_obs_async"] / t_io_sync
        t_epoch = m["T_async"] / steps
        tc = m["t_alone"] if m["t_alone"] > 0 else a[0]["tcomp_target"]
        m["overlap"] = (t_io_sync + tc - t_epoch) / min(t_io_sync, tc)
    return m


def io_sync_per_step(rows, io, mb, np_=48, tag="full"):
    xs = [r["t_total"] / r["steps"] for r in rows if r["io"] == io and r["mode"] == "sync"
          and r["kernel"] == "none" and r["nranks"] == np_ and abs(r["mib_per_rank"] - mb) < .1
          and r["tag"] == tag]
    return mean(xs) if xs else None


# ---------------------------------------------------------------- macros --
MAC = {}


def mac(name, value, fmt="{:.2f}"):
    v = fmt.format(value) if isinstance(value, (int, float, np.floating)) else value
    MAC[name] = "0" if v in ("-0", "-0.0", "-0.00") else v


def write_macros():
    with open(MACROS, "w") as f:
        f.write("% Generated by src/analysis/plots_measured.py ; do not edit.\n")
        for k in sorted(MAC):
            f.write(f"\\newcommand{{\\{k}}}{{{MAC[k]}\\xspace}}\n")


SYNC_IO = {}  # filled in main(): backend -> {kernel: sync I/O s/step}
SZ = {16: "Xs", 64: "Sm", 256: "Md", 512: "Hf", 1024: "Lg"}  # size codes (macro names are letters only)


def cname(*parts):
    """Macro-safe name: letters only."""
    s = "".join(p.replace("-", " ").title().replace(" ", "") for p in parts)
    return "".join(ch for ch in s if ch.isalpha())


# --------------------------------------------------------------- figures --
def bars(ax, groups, series, vals, err=None, width=0.8):
    n = len(series)
    w = width / n
    x = np.arange(len(groups))
    for i, s in enumerate(series):
        v = np.array(vals[s], dtype=float)
        e = None
        if err and s in err:
            lo, hi = np.array(err[s][0], dtype=float), np.array(err[s][1], dtype=float)
            e = np.vstack([np.clip(v - lo, 0, None), np.clip(hi - v, 0, None)])
            e = np.nan_to_num(e)
        ax.bar(x + (i - (n - 1) / 2) * w, v, w * 0.88, color=C[i], label=BNAME.get(s, s),
               yerr=e, error_kw=dict(lw=0.6, capsize=1.2, ecolor=INK2))
    ax.set_xticks(x, groups, rotation=30, ha="right")


def fig_main(rows):
    P = pairs(rows)
    sp, lo, hi, slow, expo = ({b: [] for b in BACKENDS} for _ in range(5))
    for b in BACKENDS:
        tio = (io_sync_per_step(rows, b, 256, np_=47, tag="pdc1") if b == "pdc"
               else io_sync_per_step(rows, b, 256))
        for k in KERNELS:
            ks = [v for kk, v in P.items() if kk[0] == b and kk[1] == k]
            if not ks:
                for d in (sp, lo, hi, slow, expo):
                    d[b].append(np.nan)
                continue
            merged = {"sync": sum((v["sync"] for v in ks), []),
                      "async": sum((v["async"] for v in ks), [])}
            m = metrics(merged, tio)
            sp[b].append(m["S"]); slow[b].append(m["slow"]); expo[b].append(m.get("exposed", np.nan))
            S_each = [s["t_total"] / a["t_total"] for s, a in zip(merged["sync"], merged["async"])]
            lo[b].append(min(S_each)); hi[b].append(max(S_each))
            mac(cname("sp", b, k), m["S"]); mac(cname("slow", b, k), m["slow"])
            mac(cname("ovl", b, k), m.get("overlap", np.nan))
            mac(cname("expo", b, k), m.get("exposed", np.nan))
            mac(cname("reps", b, k), m["n"], "{:d}")
        if tio:
            mac(cname("tio", b), tio)
    ticks = [KNAME[k] for k in KERNELS]

    fig, ax = plt.subplots(figsize=(3.4, 2.4))
    bars(ax, ticks, BACKENDS, sp, {b: (lo[b], hi[b]) for b in BACKENDS})
    ax.axhline(1, color=INK2, lw=0.8)
    ax.axhline(2.25 / 1.25, color=GRAY, lw=0.8, ls="--")
    ax.text(len(ticks) - 0.5, 2.25 / 1.25 + 0.03, "ideal overlap", color=INK2,
            fontsize=6, ha="right", va="bottom")
    ax.set_ylabel("End-to-end speedup\n(sync / async)")
    ax.set_ylim(0, 2.1)
    ax.legend(ncol=2, loc="lower center", bbox_to_anchor=(0.5, 1.0), columnspacing=0.8,
              handlelength=1.2)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "m_speedup.pdf"))
    plt.close(fig)

    fig, (a, b2) = plt.subplots(1, 2, figsize=(7.0, 1.9))
    bars(a, ticks, BACKENDS, slow)
    a.axhline(1, color=INK2, lw=0.8)
    a.set_ylabel("Compute slowdown\n($t_{comp}$ async / sync)")
    a.set_title("(a) Cost paid by the application", loc="left")
    bars(b2, ticks, BACKENDS, expo)
    b2.axhline(1, color=GRAY, lw=0.8, ls="--")
    b2.set_ylabel("Exposed I/O per step\n(async / sync I/O time)")
    b2.set_title("(b) I/O left exposed after overlap", loc="left")
    h, l = a.get_legend_handles_labels()
    fig.legend(h, l, ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.02))
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(os.path.join(OUT, "m_breakdown.pdf"))
    plt.close(fig)


def excess(rows):
    """Observed asynchronous run time with surrogate k divided by the time the
    sleep run predicts for the same compute length:
        pred_k = T_async(sleep) + n * (t_alone_k - t_alone_sleep)
    This compares asynchronous runs only, so it does not depend on the
    (noisier) synchronous runs.  Returns {backend: {kernel: ratio}}."""
    P = pairs(rows)
    out = {}
    for b in BACKENDS:
        def asy(k):
            v = [x for kk, p in P.items() if kk[0] == b and kk[1] == k for x in p["async"]]
            return v
        sl = asy("sleep")
        if not sl:
            continue
        Ts, ta_s, n = mean([r["t_total"] for r in sl]), mean([r["t_alone"] for r in sl]), sl[0]["steps"]
        out[b] = {}
        for k in KERNELS:
            if k == "sleep" or not asy(k):
                continue
            a_ = asy(k)
            pred = Ts + n * (mean([r["t_alone"] for r in a_]) - ta_s)
            out[b][k] = mean([r["t_total"] for r in a_]) / pred
    return out


def fig_excess(rows):
    ex = excess(rows)
    ks = [k for k in KERNELS if k != "sleep"]
    vals = {b: [ex.get(b, {}).get(k, np.nan) for k in ks] for b in BACKENDS}
    fig, ax = plt.subplots(figsize=(3.4, 2.4))
    bars(ax, [KNAME[k] for k in ks], BACKENDS, vals)
    ax.axhline(1, color=INK2, lw=0.8)
    ax.set_ylim(0.8, max(1.6, np.nanmax([v for b in vals for v in vals[b]]) + 0.05))
    ax.set_ylabel("Observed / sleep-predicted\nrun time (async)")
    ax.legend(ncol=2, loc="lower center", bbox_to_anchor=(0.5, 1.0), columnspacing=0.8,
              handlelength=1.2)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "m_excess.pdf"))
    plt.close(fig)
    allv = []
    for b in ex:
        v = list(ex[b].values())
        allv += v
        for k, x in ex[b].items():
            mac(cname("exc", b, k), x)
            mac(cname("excPct", b, k), (x - 1) * 100, "{:.0f}")
        mac(cname("excMinPct", b), (min(v) - 1) * 100, "{:.0f}")
        mac(cname("excMaxPct", b), (max(v) - 1) * 100, "{:.0f}")
    if allv:
        mac("ExcN", len(allv), "{:d}")
        mac("ExcLonger", sum(x > 1.0 for x in allv), "{:d}")
        mac("ExcLongerFive", sum(x >= 1.05 for x in allv), "{:d}")
        mac("ExcMaxPct", (max(allv) - 1) * 100, "{:.0f}")


def pdc_smt(rows):
    """PDC async runs whose clients also own their SMT sibling (tag pdc1smt),
    against the default placement (pdc1): compute slowdown and how much longer
    than the same placement's sleep run predicts."""
    def a(tag, k):
        return [r for r in rows if r["io"] == "pdc" and r["mode"] == "async"
                and r["tag"] == tag and r["kernel"] == k]
    sl = a("pdc1smt", "sleep")
    if not sl:
        return
    Ts, ta_s, n = sl[0]["t_total"], sl[0]["t_alone"], sl[0]["steps"]
    for k in ("spin", "dgemm"):
        x = a("pdc1smt", k)
        if not x:
            continue
        r = x[0]
        mac(cname("pdcSmtSlow", k), r["t_comp"] / r["t_alone"])
        mac(cname("pdcSmtExcPct", k), (r["t_total"] / (Ts + n * (r["t_alone"] - ta_s)) - 1) * 100, "{:.0f}")
        mac(cname("pdcSmtTotal", k), r["t_total"], "{:.0f}")
        d = a("pdc1", k)
        if d:
            mac(cname("pdcSlow", k), d[0]["t_comp"] / d[0]["t_alone"])
            mac(cname("pdcTotal", k), d[0]["t_total"], "{:.0f}")
    mac("pdcSmtSleepTotal", Ts, "{:.0f}")


def sync_io_per_step(rows):
    """Synchronous I/O time per step implied by each sync run:
    (T_sync - n * t_comp) / n."""
    P = pairs(rows)
    out = defaultdict(dict)
    for kk, p in P.items():
        if kk[1] in KERNELS:
            v = [(r["t_total"] - r["steps"] * r["t_comp"]) / r["steps"] for r in p["sync"]]
            out[kk[0]][kk[1]] = mean(v)
    for b, d in out.items():
        others = [v for k, v in d.items() if k != "sleep"]
        if "sleep" in d and others:
            mac(cname("syncIoSleep", b), d["sleep"], "{:.1f}")
            mac(cname("syncIoOtherMin", b), min(others), "{:.1f}")
            mac(cname("syncIoOtherMax", b), max(others), "{:.1f}")
    return out


def fig_decomp(rows):
    """Where the asynchronous run time goes, normalized to the sync run:
    sync compute + compute inflation + exposed I/O = T_async / T_sync."""
    P = pairs(rows)
    fig, axs = plt.subplots(1, len(BACKENDS), figsize=(7.0, 2.1), sharey=True)
    for ax, b in zip(axs, BACKENDS):
        base, infl, expo, xs = [], [], [], []
        for k in KERNELS:
            ks = [v for kk, v in P.items() if kk[0] == b and kk[1] == k]
            if not ks:
                continue
            sy = sum((v["sync"] for v in ks), [])
            asy = sum((v["async"] for v in ks), [])
            n = asy[0]["steps"]
            Ts, Ta = mean([r["t_total"] for r in sy]), mean([r["t_total"] for r in asy])
            cs, ca = mean([r["t_comp"] for r in sy]), mean([r["t_comp"] for r in asy])
            base.append(n * cs / Ts)
            infl.append(max(n * (ca - cs), 0) / Ts)
            expo.append((Ta - n * ca) / Ts)
            xs.append(KNAME[k])
            mac(cname("dcInfl", b, k), n * (ca - cs) / Ts)
            mac(cname("dcExpo", b, k), (Ta - n * ca) / Ts)
            mac(cname("dcExpoSec", b, k), (Ta - n * ca), "{:.0f}")
            mac(cname("dcInflSec", b, k), n * (ca - cs), "{:.0f}")
        x = np.arange(len(xs))
        ax.bar(x, base, 0.7, color=GRAY, label="compute (as in sync run)")
        ax.bar(x, infl, 0.7, bottom=base, color=C[1], label="compute inflation")
        ax.bar(x, expo, 0.7, bottom=np.add(base, infl), color=C[0], label="exposed I/O")
        ax.axhline(1, color=INK2, lw=0.8, ls="--")
        ax.set_xticks(x, xs, rotation=45, ha="right")
        ax.set_title(BNAME[b], loc="left")
    axs[0].set_ylabel("$T_{async}$ / $T_{sync}$")
    h, l = axs[0].get_legend_handles_labels()
    fig.legend(h, l, ncol=3, loc="upper center", bbox_to_anchor=(0.5, 1.03))
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(os.path.join(OUT, "m_decomp.pdf"))
    plt.close(fig)


def fig_size(rows):
    """Speedup vs. output size per step, one panel per backend."""
    P = pairs(rows)
    ks = ["sleep", "dgemm", "stream", "heat"]
    fig, axs = plt.subplots(1, len(BACKENDS), figsize=(7.0, 2.0), sharey=True)
    for ax, b in zip(axs, BACKENDS):
        for i, k in enumerate(ks):
            pts = sorted((kk[4] * kk[3] / 1024, metrics(v)["S"], int(kk[4])) for kk, v in P.items()
                         if kk[0] == b and kk[1] == k)
            if not pts:
                continue
            ax.plot([p_[0] for p_ in pts], [p_[1] for p_ in pts], marker="os^D"[i], ms=3.5,
                    lw=1.5, color=C[i], label=KNAME[k], markeredgecolor="white",
                    markeredgewidth=0.5)
            for gib, sp, mb in pts:
                mac(cname("size", b, k, SZ[mb]), sp)
        if not any(int(kk[4]) == 1024 for kk in P if kk[0] == b):
            ax.text(48, 0.5, "48 GiB\nnot run", transform=ax.get_xaxis_transform(),
                    ha="center", va="center", fontsize=6, color=INK2)
        ax.set_xscale("log")
        ax.set_xticks([0.75, 3, 12, 48], ["0.75", "3", "12", "48"])
        ax.minorticks_off()
        ax.axhline(1, color=INK2, lw=0.8)
        ax.set_title(BNAME[b], loc="left")
        ax.set_xlabel("GiB per step")
    axs[0].set_ylabel("End-to-end speedup")
    h, l = axs[0].get_legend_handles_labels()
    fig.legend(h, l, ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.03))
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(os.path.join(OUT, "m_size.pdf"))
    plt.close(fig)


def tio_main_rep0(main_rows, b):
    """Synchronous I/O time per step of the main phase's first I/O-only run:
    the value the sweep phase was sized from (run_matrix.phase_sweep)."""
    xs = [r["t_total"] / r["steps"] for r in main_rows if r["io"] == b and r["mode"] == "sync"
          and r["kernel"] == "none" and round(r["tcomp_target"] * 1000) == 0]
    return mean(xs) if xs else None


def fig_sweep(rows, main_rows):
    P = pairs(rows)
    fig, axs = plt.subplots(1, len(BACKENDS), figsize=(7.0, 2.0), sharey=True)
    for ax, b in zip(axs, BACKENDS):
        tio = tio_main_rep0(main_rows, b)
        if not tio:
            continue
        r = np.linspace(0.25, 3, 60)
        ax.plot(r, (r + 1) / np.maximum(r, 1), color=GRAY, ls="--", lw=1, label="ideal")
        for i, k in enumerate(["sleep", "dgemm", "stream"]):
            pts = sorted((round(kk[6] / tio, 2), metrics(v)["S"]) for kk, v in P.items()
                         if kk[0] == b and kk[1] == k)
            if pts:
                ax.plot(*zip(*pts), marker="os^"[i], ms=3.5, lw=1.5, color=C[i], label=KNAME[k],
                        markeredgecolor="white", markeredgewidth=0.5)
                best = max(pts, key=lambda t: t[1])
                mac(cname("peak", b, k), best[1])
                mac(cname("peakAt", b, k), best[0])
        # average excess of the sleep speedup over each working surrogate in
        # the regime used by sleep-based studies (t_comp >= t_io)
        sl = {round(kk[6] / tio, 2): metrics(v)["S"] for kk, v in P.items()
              if kk[0] == b and kk[1] == "sleep"}
        for k in ["dgemm", "stream"]:
            wk = {round(kk[6] / tio, 2): metrics(v)["S"] for kk, v in P.items()
                  if kk[0] == b and kk[1] == k}
            gaps = [(sl[r_] / wk[r_] - 1) * 100 for r_ in sl if r_ >= 0.99 and r_ in wk]
            if gaps:
                mac(cname("sweepGap", b, k), mean(gaps), "{:.0f}")
        ax.axhline(1, color=INK2, lw=0.8)
        ax.set_title(BNAME[b], loc="left")
        ax.set_xlabel("$t_{comp}/t_{io}$")
    axs[0].set_ylabel("End-to-end speedup")
    h, l = axs[0].get_legend_handles_labels()
    fig.legend(h, l, ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.03))
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(os.path.join(OUT, "m_sweep.pdf"))
    plt.close(fig)


def fig_bind(rows):
    """Core allocation: one panel per backend, bars = surrogate."""
    P = pairs(rows)
    # The unbound placement is excluded: without pinning, the OS placed
    # threads differently during calibration and measurement, so its
    # compute phases missed their target length (30 to 75%).
    order = [("full", 48), ("smt", 48), ("reserved", 24)]
    labels = ["fully\nsubscribed", "SMT\nsibling", "core\nreserved"]
    ks = ["sleep", "dgemm", "stream"]
    fig, axs = plt.subplots(1, len(BACKENDS), figsize=(7.0, 2.1), sharey=True)
    x = np.arange(len(order))
    for ax, b in zip(axs, BACKENDS):
        for i, k in enumerate(ks):
            v = []
            for tag, np_ in order:
                ms = [metrics(p)["S"] for kk, p in P.items()
                      if kk[0] == b and kk[1] == k and kk[2] == tag and kk[3] == np_]
                v.append(mean(ms))
                if ms:
                    mac(cname("bind", b, k, tag), mean(ms))
                sl = [metrics(p)["slow"] for kk, p in P.items()
                      if kk[0] == b and kk[1] == k and kk[2] == tag and kk[3] == np_]
                if sl:
                    mac(cname("bindslow", b, k, tag), mean(sl))
            ax.bar(x + (i - 1) * 0.27, v, 0.24, color=C[i], label=KNAME[k])
        ax.set_xticks(x, labels, fontsize=6.3)
        ax.axhline(1, color=INK2, lw=0.8)
        ax.set_title(BNAME[b], loc="left")
    axs[0].set_ylabel("End-to-end speedup")
    h, l = axs[0].get_legend_handles_labels()
    fig.legend(h, l, ncol=3, loc="upper center", bbox_to_anchor=(0.5, 1.03))
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(os.path.join(OUT, "m_bind.pdf"))
    plt.close(fig)


def fig_gpu(rows):
    """CPU vs. GPU-resident computation (4 ranks sharing one GPU):
    (a) end-to-end speedup, (b) observed async run time / sleep prediction."""
    G = pairs(rows)
    ks = ["sleep", "gpu-block", "gpu-spin", "dgemm"]

    def asy(b, k):
        return [x for kk, p in G.items() if kk[0] == b and kk[1] == k for x in p["async"]]
    gv = {b: [] for b in BACKENDS}
    ex = {b: [] for b in BACKENDS}
    for b in BACKENDS:
        sl = asy(b, "sleep")
        for k in ks:
            ms = [metrics(v)["S"] for kk, v in G.items() if kk[0] == b and kk[1] == k]
            gv[b].append(mean(ms))
            if ms:
                mac(cname("gpu", b, k), mean(ms))
            sw = [metrics(v)["slow"] for kk, v in G.items() if kk[0] == b and kk[1] == k]
            if sw:
                mac(cname("gpuslow", b, k), mean(sw))
            a_ = asy(b, k)
            if sl and a_ and k != "sleep":
                n = sl[0]["steps"]
                pred = mean([r["t_total"] for r in sl]) + n * (
                    mean([r["t_alone"] for r in a_]) - mean([r["t_alone"] for r in sl]))
                x = mean([r["t_total"] for r in a_]) / pred
                ex[b].append(x)
                mac(cname("gpuExc", b, k), x)
                mac(cname("gpuExcPct", b, k), (x - 1) * 100, "{:.0f}")
            elif k != "sleep":
                ex[b].append(np.nan)
        # synchronous I/O per step after sleep vs. after the GPU kernels
        sy = {k: [(r["t_total"] - r["steps"] * r["t_comp"]) / r["steps"]
                  for kk, p in G.items() if kk[0] == b and kk[1] == k for r in p["sync"]]
              for k in ks}
        if sy["sleep"] and sy["gpu-block"]:
            mac(cname("gpuSyncIoSleep", b), mean(sy["sleep"]))
            mac(cname("gpuSyncIoBlock", b), mean(sy["gpu-block"]))
    fig, (a, b2) = plt.subplots(1, 2, figsize=(7.0, 2.1))
    bars(a, [KNAME[k] for k in ks], BACKENDS, gv)
    a.set_xticks(np.arange(len(ks)), [KNAME[k] for k in ks], rotation=0, ha="center", fontsize=6.5)
    a.axhline(1, color=INK2, lw=0.8)
    a.set_ylabel("End-to-end speedup")
    a.set_title("(a) Speedup (sync / async)", loc="left")
    k2 = ks[1:]
    bars(b2, [KNAME[k] for k in k2], BACKENDS, ex)
    b2.set_xticks(np.arange(len(k2)), [KNAME[k] for k in k2], rotation=0, ha="center", fontsize=6.5)
    b2.axhline(1, color=INK2, lw=0.8)
    b2.set_ylim(0.6, 1.4)
    # label bars that run past the axis (e.g. PDC with DGEMM) with their value
    n_ = len(BACKENDS)
    for i, s in enumerate(BACKENDS):
        for j, v in enumerate(ex[s]):
            if np.isfinite(v) and v > 1.4:
                b2.text(j + (i - (n_ - 1) / 2) * 0.8 / n_, 1.37, f"{v:.2f}", ha="center",
                        va="top", rotation=90, fontsize=5.5, color="white")
    b2.set_ylabel("Observed / sleep-predicted\nrun time (async)")
    b2.set_title("(b) Async run time vs. sleep prediction", loc="left")
    h, l = a.get_legend_handles_labels()
    fig.legend(h, l, ncol=len(BACKENDS), loc="upper center", bbox_to_anchor=(0.5, 1.03))
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(os.path.join(OUT, "m_gpu.pdf"))
    plt.close(fig)


def tables(tera, mpiio):
    for b in BACKENDS:
        for kk, v in pairs(tera).items():
            if kk[0] == b:
                m = metrics(v)
                mac(cname("tera", b, kk[1]), m["S"])
                mac(cname("teraslow", b, kk[1]), m["slow"])
                mac(cname("teraT", b, kk[1]), m["T_sync"] / 60, "{:.1f}")
                mac(cname("teraGB", b), v["sync"][0]["gb"], "{:.0f}")
    for kk, v in pairs(mpiio).items():
        mac(cname("mpiio", kk[1]), metrics(v)["S"])


def table_rq3(rows):
    """Model, sleep, and heat speedups, plus how much longer the heat run and
    the worst surrogate take than sleep predicts: paper/table_rq3.tex."""
    P = pairs(rows)
    ex = excess(rows)

    def S(b, k):
        ks = [v for kk, v in P.items() if kk[0] == b and kk[1] == k]
        if not ks:
            return None
        return metrics({"sync": sum((v["sync"] for v in ks), []),
                        "async": sum((v["async"] for v in ks), [])})["S"]
    lines = ["% Generated by src/analysis/plots_measured.py; do not edit.",
             "\\begin{table}[t]", "\\centering", "\\small", "\\setlength{\\tabcolsep}{2.2pt}",
             "\\begin{tabular}{@{}l c c c c c c@{}}", "\\toprule",
             " & & \\multicolumn{3}{c}{Speedup} & \\multicolumn{2}{c}{Longer than sleep predicts} \\\\",
             "\\cmidrule(lr){3-5}\\cmidrule(l){6-7}",
             "Backend & $t_{comp}/t_{io}$ & Model & Sleep & Heat & Heat & Worst \\\\", "\\midrule"]
    for b in BACKENDS:
        ss, sh = S(b, "sleep"), S(b, "heat")
        if ss is None or sh is None or b not in ex:
            continue
        sio = SYNC_IO.get(b, {})
        tc = mean([r["t_comp"] for kk, p in P.items() if kk[0] == b and kk[1] in KERNELS
                   for r in p["sync"]])
        r_eff = tc / mean(list(sio.values())) if sio else float("nan")
        model = (r_eff + 1) / max(r_eff, 1)
        wk = max(ex[b], key=ex[b].get)
        eh, ew = (ex[b]["heat"] - 1) * 100, (ex[b][wk] - 1) * 100
        mac(cname("ratioEff", b), r_eff)
        mac(cname("model", b), model)
        mac(cname("worstExcKernel", b), KNAME[wk])
        lines.append(f"{BNAME[b].split()[0]} & {r_eff:.2f} & {model:.2f} & {ss:.2f} & {sh:.2f} & "
                     f"{eh:+.0f}\\% & {ew:+.0f}\\% ({KNAME[wk]}) \\\\")
    lines += ["\\bottomrule", "\\end{tabular}",
              "\\caption{Speedup predicted by the overlap model of Ravi et "
              "al.~\\cite{ravi2023evaluating} for each backend's measured ratio "
              "$t_{comp}/t_{io}$ (no overhead), and measured with \\texttt{sleep} and with "
              "the heat mini-app. The last two columns give how much longer the "
              "asynchronous run with the heat mini-app, and with the least favourable "
              "surrogate, takes than the \\texttt{sleep} run predicts for the same "
              "compute length (Section~\\ref{sec:metrics}).}",
              "\\label{tab:rq3}", "\\end{table}"]
    with open(os.path.join(ROOT, "paper", "table_rq3.tex"), "w") as f:
        f.write("\n".join(lines) + "\n")


def recheck():
    """Synchronous spin and sleep runs repeated in swapped order (spin first):
    synchronous I/O time per step after each kind of compute phase."""
    rows = load("recheck")
    if not rows:
        return
    by = defaultdict(dict)
    for r in rows:
        by[r["io"]][r["kernel"]] = (r["t_total"] - r["steps"] * r["t_comp"]) / r["steps"]
    ratios = []
    for b, d in by.items():
        if "spin" in d and "sleep" in d:
            mac(cname("recheckSpin", b), d["spin"])
            mac(cname("recheckSleep", b), d["sleep"])
            mac(cname("recheckPct", b), (d["sleep"] / d["spin"] - 1) * 100, "{:.0f}")
            ratios.append((d["sleep"] / d["spin"] - 1) * 100)
    if ratios:
        mac("RecheckMinPct", min(ratios), "{:.0f}")
        mac("RecheckMaxPct", max(ratios), "{:.0f}")


def variability(io0):
    """Run-to-run spread of the duplicated I/O-only baselines."""
    agg = defaultdict(list)
    for r in io0:
        agg[(r["io"], r["mode"], r["mib_per_rank"], r["nranks"], r["tag"])].append(r["t_total"])
    sp = sorted((max(v) - min(v)) / min(v) * 100 for v in agg.values() if len(v) >= 2)
    if sp:
        mac("VarPairs", len(sp), "{:d}")
        mac("VarMedian", float(np.median(sp)), "{:.0f}")
        mac("VarPseventyfive", sp[int(0.75 * len(sp))], "{:.0f}")
        mac("VarMax", sp[-1], "{:.0f}")


def table_io0(io0):
    """Measured I/O-only baselines (48 ranks, 5 steps): paper/table_io0.tex."""
    agg = defaultdict(list)
    for r in io0:
        if r["nranks"] == 48 and r["tag"] == "full" and r["kernel"] == "none":
            agg[(r["io"], r["mode"], r["mib_per_rank"])].append(r["t_total"] / r["steps"])
    lines = ["% Generated by src/analysis/plots_measured.py; do not edit.",
             "\\begin{table}[t]", "\\centering", "\\small", "\\setlength{\\tabcolsep}{3.5pt}",
             "\\begin{tabular}{@{}l r r r r r@{}}", "\\toprule",
             "Backend & GiB/step & Sync (s) & Async (s) & Async/sync & Sync GB/s \\\\",
             "\\midrule"]
    for b in BACKENDS:
        first = True
        for mb in (16, 64, 256, 1024):
            s_, a_ = agg.get((b, "sync", mb)), agg.get((b, "async", mb))
            if not s_ or not a_:
                continue
            gib = 48 * mb / 1024
            ts, ta = mean(s_), mean(a_)
            bw = gib * 1.073741824 / ts
            name = BNAME[b].split()[0] if first else ""
            first = False
            lines.append(f"{name} & {gib:g} & {ts:.2f} & {ta:.2f} & {ta / ts:.2f} & {bw:.1f} \\\\")
            tag = SZ[mb]
            mac(cname("ioSync", b, tag), ts)
            mac(cname("ioAsync", b, tag), ta)
            mac(cname("ioRatio", b, tag), ta / ts)
            mac(cname("ioBw", b, tag), bw, "{:.1f}")
            mac(cname("ioN", b, tag), min(len(s_), len(a_)), "{:d}")
        if b != BACKENDS[-1]:
            lines.append("\\addlinespace[2pt]")
    lines += ["\\bottomrule", "\\end{tabular}",
              "\\caption{Measured I/O-only time per step (48 ranks, 5 steps, no computation; "
              "mean of the repetitions). Async/sync below 1 means the asynchronous mode is "
              "faster even without computation to overlap.}",
              "\\label{tab:io0}", "\\end{table}"]
    with open(os.path.join(ROOT, "paper", "table_io0.tex"), "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    os.makedirs(OUT, exist_ok=True)
    io0 = load("io0")
    main_rows = load("main")
    if io0:
        table_io0(io0)
        variability(io0)
    recheck()
    if main_rows:
        # PDC (1 server on a reserved core, 47 clients) joins the main-experiment
        # figures as a fifth backend when its runs exist.
        global BACKENDS
        pdc_rows = load("pdc")
        four = BACKENDS
        if pdc_rows:
            main_rows = main_rows + pdc_rows
            BACKENDS = four + ["pdc"]
        SYNC_IO.update(sync_io_per_step(main_rows))
        fig_main(main_rows)
        fig_decomp(main_rows)
        fig_excess(main_rows)
        table_rq3(main_rows)
        pdc_smt(pdc_rows)
        BACKENDS = four
        # PDC also joins the size, sweep, and GPU figures when its phases
        # exist; its main-experiment runs (t_comp = 1.25 t_io, 11.75 GiB per
        # step) supply the matching point of the size and sweep panels.
        pdc_main = [r for r in pdc_rows if r["tag"] == "pdc1"]
        main_rows = [r for r in main_rows if r["io"] != "pdc"]
        five = four + ["pdc"]
        BACKENDS = five if load("pdcsize") else four
        if load("size"):
            fig_size(load("size") + (load("pdcsize") + pdc_main if load("pdcsize") else []))
        BACKENDS = five if load("pdcsweep") else four
        if load("sweep"):
            fig_sweep(load("sweep") + (load("pdcsweep") + pdc_main if load("pdcsweep") else []),
                      main_rows + pdc_main)
        BACKENDS = five if load("pdcgpu") else four
        if load("gpu"):
            fig_gpu(load("gpu") + load("pdcgpu"))
        BACKENDS = four
    if load("bind"):
        fig_bind(load("bind"))
    tables(load("tera"), load("mpiio"))
    write_macros()
    print(f"wrote {len(MAC)} macros to {MACROS}")


if __name__ == "__main__":
    main()
