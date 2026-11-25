#!/usr/bin/env python3
"""Summarize the traced HDF5 runs (scripts/perlmutter/hdf5.sh) and convert them for Perfetto.

    hdf5_traces.py [TRACES] [--ranks 0,1] [--out CSV] [--figdir DIR] [--no-fig]

TRACES is one job's trace directory, results/<machine>/hdf5_traces/<job>_n<nodes>
(default results/perlmutter/hdf5_traces/hdf5_trace_n2). Outputs go to that
machine's results/ and figures/ directories.

Each case directory (<placement>.<kernel>.<mode>.<flush|noflush>) holds DFTracer traces
(dft-*.pfw.gz, one per rank) and aio_bench's phase log and thread samples
(aio.<rank>.phases.csv.gz, aio.<rank>.threads.csv.gz; plain .csv is read too). Writes:

  results/<machine>/<TRACES name>_summary.csv
      one row per case, the columns below (--out)
  TRACES/<case>/perfetto.json
      the --ranks (default 0 and the first rank of the second node), for
      ui.perfetto.dev
  figures/<machine>/<TRACES name>/hdf5_trace_<kernel>_<flush>.png
      rank 0's timeline per case, with a table on the right of compute,
      observed I/O (main thread not computing), file I/O (file calls on any
      thread) and step time: total, and mean / std / min / max over the steps
      (--figdir; needs matplotlib, skipped without it)

and prints the summary as a markdown table. Per compute phase of each rank and
step, averaged over both:

  stretch    compute time / the rank's standalone time ("alone" phases)
  vs_sync    compute time / that of the sync case with the same flush mode,
             placement and kernel (the standalone time is short and can be off
             by 10% on a machine whose clock speed drifts)
  wait_ms    main thread's run-queue wait: time it was runnable but another
             thread held its CPU (0 on kernels without schedstats)
  iocpu_ms   CPU time of the I/O thread(s) (non-main threads that make MPI,
             MPI-IO, POSIX or HDF5 calls: the async VOL's execution stream)
  same_ms    the part of iocpu_ms spent on the main thread's CPU
  overlap    fraction of the compute phase during which an I/O thread was
             inside a traced call
and per step: issue_ms (main thread in the HDF5 calls), barrier_ms, and the
calls the I/O thread spent most time in.
"""
import argparse
import bisect
import csv
import glob
import gzip
import json
import os
import re
import sys
from collections import defaultdict
from statistics import mean, pstdev

HERE = os.path.dirname(os.path.abspath(__file__))
IO_CATS = re.compile(r"^(POSIX|STDIO|MPIIO|h5.*|collective|comm|datatype|p2p)$")


def opentext(path):
    return (gzip.open if path.endswith(".gz") else open)(path, "rt")


def load_dft(path):
    """Complete events and the rank of one DFTracer trace."""
    rank, events = None, []
    with opentext(path) as f:
        for line in f:
            line = line.strip().rstrip(",")
            if not line.startswith("{"):
                continue
            e = json.loads(line)
            if e.get("ph") == 4 and e.get("name") == "PR" and e["args"].get("name") == "rank":
                rank = int(e["args"]["value"])
            elif e.get("ph") == 1:
                events.append(e)
    return rank, events


def load_threads(path):
    """Samples per tid as sorted (ts_us, cpu, run_ns, wait_ns), and the sampler's tid."""
    samples, sampler = defaultdict(list), None
    with opentext(path) as f:
        rows = []
        for line in f:
            if line.startswith("#"):
                m = re.search(r"tid=(\d+) role=sampler", line)
                if m:
                    sampler = int(m.group(1))
            else:
                rows.append(line)
    for r in csv.DictReader(rows):
        samples[int(r["tid"])].append((float(r["ts_us"]), int(r["cpu"]), int(r["run_ns"]), int(r["wait_ns"])))
    for v in samples.values():
        v.sort()
    return samples, sampler


def at(series, t, i, ts):
    """Cumulative counter i of a thread's samples, linearly interpolated at t
    (ts: the samples' timestamps)."""
    j = bisect.bisect_left(ts, t)
    if j == 0:
        return series[0][i] if series and t >= series[0][0] else 0.0
    if j == len(series):
        return series[-1][i]
    (t0, *a), (t1, *b) = series[j - 1], series[j]
    return a[i - 1] + (b[i - 1] - a[i - 1]) * (t - t0) / (t1 - t0) if t1 > t0 else b[i - 1]


def merge(intervals):
    """Union of intervals as a sorted list of disjoint ones."""
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        elif b > a:
            out.append([a, b])
    return [tuple(x) for x in out]


def overlap_intervals(rank_phases, io_iv):
    """Where a compute phase and an I/O thread's calls run at the same time."""
    comp = merge((a, b) for n, _, a, b, _ in rank_phases if n == "compute")
    io = merge(io_iv)
    out, j = [], 0
    for a, b in comp:
        while j < len(io) and io[j][1] <= a:
            j += 1
        k = j
        while k < len(io) and io[k][0] < b:
            lo, hi = max(a, io[k][0]), min(b, io[k][1])
            if hi > lo:
                out.append((lo, hi))
            k += 1
    return out


def busy(intervals, lo, hi):
    """Length of the union of intervals clipped to [lo, hi]."""
    total, end = 0.0, lo
    for a, b in sorted(intervals):
        a, b = max(a, end), min(b, hi)
        if b > a:
            total += b - a
            end = b
    return total


def cpu_on(series, t0, t1, cpu_of_main, ts):
    """CPU time (ns) a thread used in [t0, t1] while on the main thread's CPU."""
    pts = series[bisect.bisect_left(ts, t0):bisect.bisect_right(ts, t1)]
    total = 0.0
    for (ta, _, ra, _), (tb, cb, rb, _) in zip(pts, pts[1:]):
        if cb == cpu_of_main(tb):
            total += rb - ra
    return total


def analyze_case(d):
    phases = defaultdict(list)  # rank -> rows
    for f in glob.glob(os.path.join(d, "aio.*.phases.csv*")):
        for r in csv.DictReader(opentext(f)):
            phases[int(r["rank"])].append(
                (r["name"], int(r["step"]), float(r["ts_us"]), float(r["ts_us"]) + float(r["dur_us"]), int(r["pid"])))
    threads = {}
    for f in glob.glob(os.path.join(d, "aio.*.threads.csv*")):
        threads[int(re.search(r"aio\.(\d+)\.threads", os.path.basename(f)).group(1))] = load_threads(f)
    dft = {}
    for f in glob.glob(os.path.join(d, "dft-*.pfw*")):
        rank, ev = load_dft(f)
        if rank is not None:
            dft[rank] = ev
    rows, calls = [], defaultdict(float)
    roles = {}
    for rank, ph in sorted(phases.items()):
        pid = ph[0][4]
        events = dft.get(rank, [])
        samples, sampler = threads.get(rank, ({}, None))
        io_tids = {e["tid"] for e in events if e["tid"] not in (pid, sampler) and IO_CATS.match(e.get("cat", ""))}
        roles[rank] = {pid: "main", sampler: "sampler", **{t: "I/O thread" for t in io_tids}}
        io_iv = [(e["ts"], e["ts"] + e["dur"]) for e in events if e["tid"] in io_tids]
        alone = [b - a for n, _, a, b, _ in ph if n == "alone"]
        alone = mean(alone) if alone else None
        main = samples.get(pid, [])
        ts = {tid: [x[0] for x in ser] for tid, ser in samples.items()}
        main_ts = ts.get(pid, [])

        def main_cpu(t):
            j = bisect.bisect_left(main_ts, t)
            return main[min(j, len(main) - 1)][1] if main else -1

        for name, step, a, b, _ in ph:
            if name != "compute":
                continue
            issue = [(x, y) for n, s, x, y, _ in ph if s == step and n == "issue"]
            barrier = [(x, y) for n, s, x, y, _ in ph if s == step and n == "barrier"]
            r = {"rank": rank, "step": step, "comp_ms": (b - a) / 1e3,
                 "stretch": (b - a) / alone if alone else float("nan"),
                 "wait_ms": (at(main, b, 3, main_ts) - at(main, a, 3, main_ts)) / 1e6 if main else 0.0,
                 "iocpu_ms": sum(at(samples[t], b, 2, ts[t]) - at(samples[t], a, 2, ts[t])
                                 for t in io_tids if t in samples) / 1e6,
                 "same_ms": sum(cpu_on(samples[t], a, b, main_cpu, ts[t]) for t in io_tids if t in samples) / 1e6,
                 "overlap": busy(io_iv, a, b) / (b - a) if b > a else 0.0,
                 "issue_ms": sum(y - x for x, y in issue) / 1e3,
                 "barrier_ms": sum(y - x for x, y in barrier) / 1e3}
            rows.append(r)
        for e in events:
            if e["tid"] in io_tids:
                calls[f"{e['cat']}:{e['name']}"] += e["dur"] / 1e3 / max(len(phases), 1)
    return rows, calls, phases, dft, threads, roles


PHASE_TID = 1 << 30  # synthetic thread ids of a rank's "aio phases" and overlap tracks
OVERLAP_TID = PHASE_TID + (1 << 20)
OVERRUN_US = 50


def nest(slices):
    """Clip slices that end up to OVERRUN_US after the slice enclosing them on
    the same track. Such overruns come from timestamp rounding (back-to-back
    phases) and µs-resolution timers, and Perfetto cannot nest a slice that
    crosses its parent's end. Larger overlaps are left for Perfetto to show."""
    tracks = defaultdict(list)
    for e in slices:
        # whole microseconds: epoch-µs floats resolve only ~0.25 µs
        end = round(e["ts"] + e["dur"])
        e["ts"] = round(e["ts"])
        e["dur"] = max(end - e["ts"], 0)
        tracks[(e["pid"], e["tid"])].append(e)
    for evs in tracks.values():
        evs.sort(key=lambda e: (e["ts"], -e["dur"]))
        stack = []
        for e in evs:
            while stack:
                p = stack[-1]
                end = p["ts"] + p["dur"]
                if end <= e["ts"]:
                    stack.pop()
                elif e["ts"] + e["dur"] > end + OVERRUN_US and end - e["ts"] <= OVERRUN_US:
                    p["dur"] = e["ts"] - p["ts"]  # back-to-back: the earlier one ends where e starts
                    stack.pop()
                else:
                    break
            if stack:
                end = stack[-1]["ts"] + stack[-1]["dur"]
                if 0 < e["ts"] + e["dur"] - end <= OVERRUN_US:
                    e["dur"] = end - e["ts"]
            stack.append(e)
    return slices


def perfetto(d, ranks, phases, dft, threads, roles):
    out = []
    for rank in ranks:
        if rank not in phases:
            continue
        pid = phases[rank][0][4]
        out.append({"ph": "M", "name": "process_name", "pid": rank, "args": {"name": f"rank {rank}"}})
        names = dict(roles.get(rank, {}))
        samples, _ = threads.get(rank, ({}, None))
        for tid in set(samples) | {e["tid"] for e in dft.get(rank, [])} | {pid}:
            out.append({"ph": "M", "name": "thread_name", "pid": rank, "tid": tid,
                        "args": {"name": f"{names.get(tid, 'thread')} {tid}"}})
        # aio_bench's phases get their own track: their clock and DFTracer's
        # differ by microseconds, so on the main thread's track their edges
        # would cross those of the HDF5 calls.
        ptid = PHASE_TID + rank
        out.append({"ph": "M", "name": "thread_name", "pid": rank, "tid": ptid, "args": {"name": "aio phases"}})
        slices = [{"ph": "X", "name": n, "cat": "aio", "pid": rank, "tid": ptid, "ts": a, "dur": b - a,
                   "args": {"step": s}} for n, s, a, b, _ in phases[rank]]
        slices += [{"ph": "X", "name": e["name"], "cat": e.get("cat", ""), "pid": rank, "tid": e["tid"],
                    "ts": e["ts"], "dur": e["dur"], "args": e.get("args", {})} for e in dft.get(rank, [])]
        # compute and I/O thread calls at the same time
        io_tids = {t for t, r in names.items() if r == "I/O thread"}
        otid = OVERLAP_TID + rank
        out.append({"ph": "M", "name": "thread_name", "pid": rank, "tid": otid,
                    "args": {"name": "overlap: compute while the I/O thread is in a call"}})
        for a, b in overlap_intervals(phases[rank], [(e["ts"], e["ts"] + e["dur"]) for e in dft.get(rank, [])
                                                      if e["tid"] in io_tids]):
            slices.append({"ph": "X", "name": "overlap", "cat": "aio", "pid": rank, "tid": otid, "ts": a,
                           "dur": b - a, "args": {}})
        out += nest(slices)
        for tid, ser in samples.items():
            label = f"{names.get(tid, 'thread')} {tid}"
            for (ta, _, ra, wa), (tb, cb, rb, wb) in zip(ser, ser[1:]):
                dt = (tb - ta) * 1e3  # ns
                if dt <= 0:
                    continue
                out.append({"ph": "C", "name": f"{label} CPU %", "pid": rank, "ts": tb,
                            "args": {"run": round(100 * (rb - ra) / dt, 1), "runqueue wait": round(100 * (wb - wa) / dt, 1)}})
                out.append({"ph": "C", "name": f"{label} on CPU", "pid": rank, "ts": tb, "args": {"cpu": cb}})
    with open(os.path.join(d, "perfetto.json"), "w") as f:
        json.dump({"traceEvents": out, "displayTimeUnit": "ms"}, f)


def step_table(ph, segs, io_iv, first, t1):
    """Text table for one row of the timeline figure: total over the run and
    mean / std / min / max over the steps, in seconds. Post-step (drain,
    close, final fsync) is not part of any step and is listed on its own."""
    steps = [(a, b) for name, a, b in segs if name.startswith("step ")]
    comp = [sum(min(y, b) - max(x, a) for n, _, x, y, _ in ph if n == "compute" and x < b and y > a)
            for a, b in steps]
    io = merge(io_iv)
    fio = [sum(max(0, min(y, b) - max(x, a)) for x, y in io) for a, b in steps]
    span = [b - a for a, b in steps]
    obs = [t - c for t, c in zip(span, comp)]
    post = t1 - steps[-1][1] if steps else 0
    lines = [f"{'':13s}{'total':>7s}{'avg':>7s}{'std':>7s}{'min':>7s}{'max':>7s}"]
    for label, v in (("compute", comp), ("observed I/O", obs), ("file I/O", fio), ("step", span)):
        v = [x / 1e6 for x in v]
        lines.append(f"{label:13s}{sum(v):7.2f}{mean(v):7.2f}{pstdev(v):7.2f}{min(v):7.2f}{max(v):7.2f}"
                     if v else f"{label:13s}")
    lines.append(f"{'post-step':13s}{post / 1e6:7.2f}")
    lines.append(f"{'run total':13s}{(t1 - first) / 1e6:7.2f}")
    lines.append("seconds; avg/std/min/max over steps")
    return "\n".join(lines)


def figure(kernel, flush, cases, outdir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
    COL = {"compute": "#2a78d6", "issue": "#f1dccb", "barrier": "#c9c8c3", "io": "#c2337a", "drain": "#f1dccb",
           "overlap": "#e34948"}
    # main-thread HDF5 calls inside the issue and final drain phases (aio_bench's h5.* phases)
    H5 = [("h5.wait", "H5ESwait", "#f2a93b"), ("h5.dcreate", "H5Dcreate_async", "#7a5cc8"),
          ("h5.dwrite", "H5Dwrite_async", "#eb6834"), ("h5.dclose", "H5Dclose_async", "#1baf7a"),
          ("h5.flush", "H5Fflush_async", "#7b2d0f"), ("h5.fclose", "H5Fclose_async", "#3fb8c4"),
          ("final.fsync", "fsync after the close", "#3d1405")]
    COL.update({k: c for k, _, c in H5})
    STEPCOL = ("#6b6a66", "#e4e3df", "#cfcdc7")  # pre/post-step, then alternating steps
    COL["prepost"] = STEPCOL[0]
    plt.rcParams.update({"figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
                         "axes.edgecolor": GRID, "text.color": INK, "xtick.color": INK2, "ytick.color": INK2,
                         "font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.spines.left": False})
    height = 1.75 * len(cases) + 1.9  # 1.3 in at the bottom for the legend
    fig, axs = plt.subplots(len(cases), 1, figsize=(15, height), squeeze=False, sharex=True)
    xmax = 0.0
    for ax, (label, (rows, _, phases, dft, threads, roles)) in zip(axs[:, 0], cases):
        rank = min(phases)
        ph = phases[rank]
        pid = ph[0][4]
        loop = [p for p in ph if p[0] in ("compute", "issue", "barrier", "drain")]
        first, t1 = min(p[2] for p in loop), max(p[3] for p in loop)
        # pre-step: from the end of the standalone baseline to the first step
        # (file create, thread pinning, the barrier before the loop)
        alone_end = max((b for n, _, a, b, _ in ph if n == "alone" and b <= first), default=first)
        t0 = alone_end
        xmax = max(xmax, (t1 - t0) / 1e6)
        segs = [("pre-step", t0, first)] if first > t0 else []
        for st in sorted({s for n, s, *_ in loop if n == "compute"}):
            a = min(x for n, s, x, _, _ in loop if s == st and n == "compute")
            b = max(y for n, s, _, y, _ in loop if s == st and n in ("compute", "issue", "barrier"))
            segs.append((f"step {st}", a, b))
        # post-step: drain (wait for the last write), file close, and with
        # endflush the final flush (fsync after the close)
        for _, _, a, b, _ in [p for p in loop if p[0] == "drain"]:
            cl = min((x for n, s, x, y, _ in ph if n == "h5.fclose" and s == -1 and a <= x <= b), default=b)
            fl = [(x, y) for n, s, x, y, _ in ph if n == "final.fsync" and a <= x <= b]
            segs.append(("drain", a, cl))
            segs.append(("close", cl, fl[0][0] if fl else b))
            if fl:
                segs.append(("final flush", fl[0][0], fl[0][1]))
        for i, (name, a, b) in enumerate(segs):
            dark = not name.startswith("step ")
            color = COL["final.fsync"] if name == "final flush" else STEPCOL[0] if dark else STEPCOL[1 + i % 2]
            ax.broken_barh([((a - t0) / 1e6, (b - a) / 1e6)], (3.1, 0.8), lw=0, color=color)
            if b - a >= 0.05 * (t1 - t0):  # label only segments wide enough to hold it
                ax.text((a + b) / 2e6 - t0 / 1e6, 3.5, name, ha="center", va="center", fontsize=7.5,
                        color=SURFACE if dark else INK)
        for n, _, a, b, _ in loop + [p for p in ph if p[0] in COL and p[0].startswith("h5.") and p[2] >= t0]:
            ax.broken_barh([((a - t0) / 1e6, (b - a) / 1e6)], (2.1, 0.8), color=COL[n], lw=0)
        io_tids = {t for t, r in roles[rank].items() if r == "I/O thread"}
        # file I/O of the I/O thread(s), plus the main thread's own (sync), within the timed loop
        iv = [(max(e["ts"], t0), min(e["ts"] + e["dur"], t1)) for e in dft.get(rank, [])
              if e["tid"] in io_tids or (e["tid"] == pid and e.get("cat") in ("MPIIO", "POSIX"))]
        ax.broken_barh([((a - t0) / 1e6, (b - a) / 1e6) for a, b in iv if b > a], (1.1, 0.8), color=COL["io"], lw=0)
        ov = overlap_intervals(ph, [(e["ts"], e["ts"] + e["dur"]) for e in dft.get(rank, []) if e["tid"] in io_tids])
        ax.broken_barh([((a - t0) / 1e6, (b - a) / 1e6) for a, b in ov], (0.1, 0.8), color=COL["overlap"], lw=0)
        comp = sum(b - a for n, _, a, b, _ in ph if n == "compute")
        rs = [r for r in rows if r["rank"] == rank]
        s = mean(r["stretch"] for r in rs) if rs else float("nan")
        w = mean(r["wait_ms"] for r in rs) if rs else 0
        ax.set_yticks([0.5, 1.5, 2.5, 3.5], ["overlap", "file I/O", "main", "step"])
        ax.set_ylim(0, 4)
        ax.set_title(f"{label}: compute x{s:.2f} of alone, overlaps I/O {100 * sum(b - a for a, b in ov) / comp:.0f}%"
                     f" of the time, main run-queue wait {w:.0f} ms/step", fontsize=9, loc="left", color=INK)
        ax.tick_params(axis="y", length=0)
        ax.text(1.015, 0.5, step_table(ph, segs, iv, first, t1), transform=ax.transAxes, ha="left",
                va="center", fontsize=7.5, family="monospace", color=INK)
    axs[-1, 0].set_xlim(0, xmax * 1.01)
    axs[-1, 0].set_xlabel("seconds from the start of the pre-step (rank 0)", color=INK2)
    keys = ["prepost", "compute", "barrier"] + [k for k, _, _ in H5] + ["issue", "io", "overlap"]
    labels = ["step row: pre-step (file create), drain (wait for the last write), close", "compute",
              "barrier"] + [f"main in {name}" + (" (step row: final flush)" if k == "final.fsync" else "")
                            for k, name, _ in H5] + [
        "other main-thread time in the I/O phase", "MPI-IO / POSIX on the file (any thread)",
        "compute while the I/O thread is in a call"]
    handles = [plt.Rectangle((0, 0), 1, 1, color=COL[k]) for k in keys]
    fig.legend(handles, labels, loc="lower center", ncol=3, frameon=False, bbox_to_anchor=(0.5, 0), fontsize=8.5)
    fig.suptitle(f"HDF5, {kernel} kernel, " + {"flush": "H5Fflush every step",
                                               "endflush": "one fsync after the last step",
                                               "noflush": "no H5Fflush"}.get(flush, flush), fontsize=11,
                 x=0.34)  # over the timelines, not the tables
    fig.tight_layout(rect=(0, 1.3 / height, 0.68, 1))  # right third for the tables
    path = os.path.join(outdir, f"hdf5_trace_{kernel}_{flush}.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(path, file=sys.stderr)


def main(argv):
    ap = argparse.ArgumentParser(description="Summarize traced HDF5 runs; see the module docstring.")
    ap.add_argument("traces", nargs="?", default=os.path.join(HERE, "../../results/perlmutter/hdf5_traces/hdf5_trace_n2"))
    ap.add_argument("--ranks", help="ranks in perfetto.json, e.g. 0,1 (default 0 and the first rank of node 2)")
    ap.add_argument("--out", help="summary CSV (default results/<machine>/<traces name>_summary.csv)")
    ap.add_argument("--figdir", help="default figures/<machine>/<traces name>")
    ap.add_argument("--no-fig", action="store_true")
    opt = ap.parse_args(argv[1:])
    root = opt.traces
    m = re.search(r"results/([^/]+)/hdf5_traces/", os.path.abspath(root) + "/")
    machine = m.group(1) if m else "perlmutter"
    # one folder per run: figures/<machine>/<job>_n<nodes>/
    opt.figdir = opt.figdir or os.path.join(HERE, "../../figures", machine, os.path.basename(os.path.normpath(root)))
    ranks = [int(x) for x in opt.ranks.split(",")] if opt.ranks else None
    cases, summary = {}, []
    for d in sorted(glob.glob(os.path.join(root, "*.*.*.*"))):
        if not os.path.isfile(os.path.join(d, "out.log")):  # hdf5.sh copies it in when the case ends
            continue
        place, kernel, mode, flush = os.path.basename(d).split(".")
        res = analyze_case(d)
        rows, calls = res[0], res[1]
        if not rows:
            continue
        nranks = len(res[2])
        perfetto(d, ranks or sorted({0, 32 if nranks > 32 else nranks // 2}), *res[2:])
        # the figure draws only the lowest rank; keeping every rank's DFTracer
        # events for all cases takes more memory than a laptop has
        r0 = min(res[2])
        cases[(place, kernel, mode, flush)] = (rows, calls, {r0: res[2][r0]}, {r0: res[3].get(r0, [])},
                                               None, res[5])
        top = sorted(calls.items(), key=lambda kv: -kv[1])[:3]
        summary.append({"flush": flush, "placement": place, "kernel": kernel, "mode": mode, "ranks": nranks,
                        **{k: mean(r[k] for r in rows) for k in
                           ("comp_ms", "stretch", "wait_ms", "iocpu_ms", "same_ms", "overlap", "issue_ms", "barrier_ms")},
                        "io_thread_top_calls": "; ".join(f"{k} {v:.0f} ms" for k, v in top)})
    if not summary:
        sys.exit(f"no traced cases under {root}")
    summary.sort(key=lambda r: (r["flush"], r["placement"], r["kernel"], r["mode"] != "sync"))
    sync_comp = {(r["flush"], r["placement"], r["kernel"]): r["comp_ms"] for r in summary if r["mode"] == "sync"}
    for r in summary:
        base = sync_comp.get((r["flush"], r["placement"], r["kernel"]))
        r["vs_sync"] = r["comp_ms"] / base if base else float("nan")
    out = opt.out or os.path.join(HERE, "../../results", machine,
                                  os.path.basename(os.path.normpath(root)) + "_summary.csv")
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0]))
        w.writeheader()
        w.writerows(summary)
    cols = ["flush", "placement", "kernel", "mode", "stretch", "vs_sync", "wait_ms", "iocpu_ms", "same_ms", "overlap", "issue_ms",
            "barrier_ms", "io_thread_top_calls"]
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for s in summary:
        print("| " + " | ".join(f"{s[c]:.2f}" if isinstance(s[c], float) else str(s[c]) for c in cols) + " |")
    if opt.no_fig:
        return
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        print("matplotlib not found, no figures", file=sys.stderr)
        return
    outdir = opt.figdir
    os.makedirs(outdir, exist_ok=True)
    order = {"shared": 0, "reserved": 1, "iocore": 2}
    for kernel, flush in sorted({(k[1], k[3]) for k in cases}):
        sel = sorted((key for key in cases if key[1] == kernel and key[3] == flush),
                     key=lambda k: (order.get(k[0], 9), k[2] != "sync"))
        figure(kernel, flush, [(f"{p} {m}", cases[key]) for key in sel for p, _, m, _ in [key]], outdir)


if __name__ == "__main__":
    main(sys.argv)
