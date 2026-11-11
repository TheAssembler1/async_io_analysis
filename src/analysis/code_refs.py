#!/usr/bin/env python3
"""Locate the code segments the paper refers to and emit LaTeX.

    python3 src/analysis/code_refs.py

Writes
  paper/code_lines.tex  \\cl<Name> ("first-last") and \\src<Name> ("Listing N, lines a-b")
  paper/code_table.tex  the code-map table (segment, listing, lines,
                        description, section) included in the appendix

Segments are found by pattern, not by fixed line numbers, so references stay
correct when the code changes.  Run before compiling the paper.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, ".."))
PAPER = os.path.normpath(os.path.join(HERE, "..", "..", "paper"))

# end rules: "c"   brace-matched C block starting at the first '{'
#            "py"  Python def: until the next non-indented, non-blank line
#            "sh"  shell function: until a line that is exactly '}'
#            "1"   the start line only
#            "eof" to the end of the file
#            other string: regex for the (inclusive) last line
# (name, listing label, file, start regex, end rule, description, section label)
SEGMENTS = [
    # --- driver
    ("HeaderBackend", "header", "aio_bench.h", r"^typedef struct io_backend", "c",
     "Backend interface: init, issue, wait, fini, compute hints", "sec:impl"),
    ("ThreadInit", "driver", "aio_bench.c", r"MPI_Init_thread", "1",
     "MPI initialized with \\texttt{MPI\\_THREAD\\_MULTIPLE} for every backend", "sec:impl"),
    ("TimedMax", "driver", "aio_bench.c", r"^static double timed_max", "c",
     "Concurrent kernel run timed as the maximum over ranks", "sec:surrogates"),
    ("Calibrate", "driver", "aio_bench.c", r"^static double calibrate", "c",
     "Calibration of fixed work to the target $t_{comp}$", "sec:surrogates"),
    ("Baseline", "driver", "aio_bench.c", r"---- calibration and standalone", r"t_alone \+= timed_max",
     "Calibration call and standalone baseline $t_{comp}^{alone}$", "sec:surrogates"),
    ("Loop", "driver", "aio_bench.c", r"---- timed epoch loop", r"double total = t_end - t0",
     "Timed epoch loop: compute, then issue (sync or async) I/O", "sec:workload"),
    ("Reduce", "driver", "aio_bench.c", r"---- reduce:", r"MPI_Reduce\(&total",
     "Per-step maxima over ranks and end-to-end time", "sec:metrics"),
    # --- kernels
    ("Sleep", "kernels", "kernels.c", r"case K_SLEEP", r"^\s+break;",
     "\\texttt{sleep}: \\texttt{nanosleep} for $t_{comp}$", "sec:surrogates"),
    ("Spin", "kernels", "kernels.c", r"^static void run_spin", "c",
     "spin: register-only xorshift loop", "sec:surrogates"),
    ("Dgemm", "kernels", "kernels.c", r"^static void run_dgemm", "c",
     "DGEMM: $256^2$ OpenBLAS calls (L2-resident)", "sec:surrogates"),
    ("Stream", "kernels", "kernels.c", r"^static void run_stream", "c",
     "STREAM triad over $3\\times32$\\,MiB arrays", "sec:surrogates"),
    ("Cart", "kernels", "kernels.c", r"^static void setup_cart", "c",
     "3-D periodic Cartesian decomposition", "sec:surrogates"),
    ("Exchange", "kernels", "kernels.c", r"^static void exchange", "c",
     "Six-neighbour non-blocking face exchange", "sec:surrogates"),
    ("Halo", "kernels", "kernels.c", r"^static void run_halo", "c",
     "halo: 512\\,KiB face exchange plus allreduce", "sec:surrogates"),
    ("HeatHalo", "kernels", "kernels.c", r"^static void heat_halo", "c",
     "heat: pack, exchange, and unpack ghost faces", "sec:surrogates"),
    ("Heat", "kernels", "kernels.c", r"^static void run_heat", "c",
     "heat: 7-point Jacobi sweep on $128^3$ cells", "sec:surrogates"),
    ("GpuKernel", "gpu", "gpu_kernels.cu", r"^__global__ void fma_kernel", "c",
     "GPU FMA kernel", "sec:surrogates"),
    ("GpuSetup", "gpu", "gpu_kernels.cu", r"gpu_setup\(int blocking_sync", "c",
     "Host wait mode: spin vs.\\ blocking synchronization", "sec:rq4"),
    ("GpuRun", "gpu", "gpu_kernels.cu", r"gpu_run\(double units\)", "c",
     "GPU work units and stream synchronization", "sec:surrogates"),
    # --- backends
    ("PosixInit", "posix", "io_posix.c", r"^static int posix_init", "c",
     "POSIX AIO with one glibc helper thread", "sec:impl"),
    ("PosixWait", "posix", "io_posix.c", r"^static void posix_wait", "c",
     "Completion of the in-flight \\texttt{aio\\_write}", "sec:impl"),
    ("PosixIssue", "posix", "io_posix.c", r"^static void posix_issue", "c",
     "Blocking \\texttt{pwrite} vs.\\ staged \\texttt{aio\\_write}", "sec:impl"),
    ("MpiioIssue", "mpiio", "io_mpiio.c", r"^static void mpiio_issue", "c",
     "Collective \\texttt{MPI\\_File\\_write\\_at\\_all} vs.\\ \\texttt{iwrite\\_at\\_all}", "sec:impl"),
    ("HdfInit", "hdf5", "io_hdf5.c", r"^static int hdf5_init", "c",
     "MPI-IO VFD, collective transfer, event set, VOL check", "sec:impl"),
    ("HdfWait", "hdf5", "io_hdf5.c", r"^static void hdf5_wait", "c",
     "\\texttt{H5ESwait} on the event set", "sec:impl"),
    ("HdfIssue", "hdf5", "io_hdf5.c", r"^static void hdf5_issue", "c",
     "Per-step dataset create/write/close via the \\texttt{\\_async} API", "sec:impl"),
    ("BpInit", "adios2", "io_adios2.c", r"^static int bp5_init", "c",
     "BP5 setup: \\texttt{AsyncWrite} mode and aggregation parameters", "sec:impl"),
    ("BpIssue", "adios2", "io_adios2.c", r"^static void bp5_issue", "c",
     "One ADIOS2 step per timestep (deferred \\texttt{Put})", "sec:impl"),
    ("BpHints", "adios2", "io_adios2.c", r"^static void bp5_cbegin", r"^const io_backend",
     "Computation-block hints for Guided mode", "sec:impl"),
    ("PdcIssue", "pdc", "io_pdc.c", r"^static void pdc_issue", "c",
     "PDC region transfer (implemented, not evaluated)", "sec:impl"),
    # --- scripts
    ("Binding", "runone", "scripts/run_one.sh", r"^case \$bind in", r"^esac",
     "Rank binding: fully subscribed, SMT sibling, core reserved", "sec:machine"),
    ("AsyncEnv", "runone", "scripts/run_one.sh", r"HDF5_VOL_CONNECTOR", r"AIO_HDF5_INDEP",
     "Async VOL selection, \\texttt{EXE\\_DCLOSE}, collective HDF5", "sec:impl"),
    ("Cgroup", "runone", "scripts/run_one.sh", r"^sync", r'"\$@"$',
     "\\texttt{sync}, cgroup memory limit, and launch", "sec:machine"),
    ("MatrixEnv", "runmatrix", "scripts/run_matrix.py", r"^ENV = \{", r"^\}",
     "Library settings shared by all runs", "sec:impl"),
    ("MemMax", "runmatrix", "scripts/run_matrix.py", r"^def memmax", "py",
     "Per-run cgroup limit: footprint + 24\\,GiB", "sec:machine"),
    ("IoBaseline", "runmatrix", "scripts/run_matrix.py", r"^def baseline", "py",
     "Per-phase I/O-only baseline that sizes $t_{comp}$", "sec:workload"),
    ("TioSync", "runmatrix", "scripts/run_matrix.py", r"^def tio_sync", "py",
     "Synchronous I/O time per step from the baseline runs", "sec:workload"),
    ("RunOne", "runmatrix", "scripts/run_matrix.py", r"^def run\(", "py",
     "Resumable execution of one configuration", "sec:workload"),
    ("PhaseMain", "runmatrix", "scripts/run_matrix.py", r"^def phase_main", "py",
     "Main experiment: all CPU surrogates, one run each", "sec:rq1"),
    ("PhaseSize", "runmatrix", "scripts/run_matrix.py", r"^def phase_size", "py",
     "Size sweep, 0.75 to 48\\,GiB per step", "sec:workload"),
    ("PhaseSizeLarge", "runmatrix", "scripts/run_matrix.py", r"^def phase_sizelarge", "py",
     "48\\,GiB per step for POSIX and ADIOS2", "sec:workload"),
    ("PhaseSweep", "runmatrix", "scripts/run_matrix.py", r"^def phase_sweep", "py",
     "$t_{comp}/t_{io}$ sweep", "sec:rq2"),
    ("PhaseBind", "runmatrix", "scripts/run_matrix.py", r"^def phase_bind", "py",
     "Core-allocation experiment", "sec:rq4"),
    ("PhaseGpu", "runmatrix", "scripts/run_matrix.py", r"^def phase_gpu", "py",
     "GPU experiment (4 ranks)", "sec:rq4"),
    ("RunPdc", "runmatrix", "scripts/run_matrix.py", r"^def run_pdc", "py",
     "One PDC run, killed as a group after 30 minutes", "sec:impl"),
    ("PhasePdc", "runmatrix", "scripts/run_matrix.py", r"^def phase_pdc", "py",
     "PDC main experiment and SMT-sibling placement", "sec:rq4"),
    ("PhasePdcSize", "runmatrix", "scripts/run_matrix.py", r"^def phase_pdcsize", "py",
     "PDC size sweep, 0.73 and 2.9\\,GiB per step", "sec:size"),
    ("PhasePdcSweep", "runmatrix", "scripts/run_matrix.py", r"^def phase_pdcsweep", "py",
     "PDC $t_{comp}/t_{io}$ sweep, 0.25 to 1", "sec:sweep"),
    ("PhasePdcGpu", "runmatrix", "scripts/run_matrix.py", r"^def phase_pdcgpu", "py",
     "PDC GPU experiment (1 server, 4 clients)", "sec:gpu"),
    ("Metrics", "plotsmeasured", "analysis/plots_measured.py", r"^def metrics", "py",
     "Speedup, compute slowdown, and exposed I/O", "sec:metrics"),
    ("Excess", "plotsmeasured", "analysis/plots_measured.py", r"^def excess", "py",
     "Run time relative to the sleep prediction", "sec:metrics"),
    ("BuildHdf", "builddeps", "scripts/build_deps.sh", r"^build_hdf5\(\)", "sh",
     "Parallel, thread-safe HDF5 build", "sec:machine"),
    ("BuildVol", "builddeps", "scripts/build_deps.sh", r"^build_volasync\(\)", "sh",
     "Async VOL build with the wait patch", "sec:impl"),
    ("BuildAdios", "builddeps", "scripts/build_deps.sh", r"^build_adios2\(\)", "sh",
     "ADIOS2 build options", "sec:machine"),
    ("VolPatch", "volasyncpatch", "scripts/vol-async-wait.patch", r"^diff", "eof",
     "Async VOL wait back-off: 1\\,s and 100\\,ms $\\to$ 1\\,ms", "sec:impl"),
    ("AdiosPatch", "adiospatch", "scripts/adios2-fsync.patch", r"^diff", "eof",
     "Opt-in per-step \\texttt{fdatasync} for BP5 (inactive)", "sec:machine"),
]

GROUPS = [("Driver", "header"), ("", "driver"), ("Compute surrogates", "kernels"), ("", "gpu"),
          ("I/O backends", "posix"), ("", "mpiio"), ("", "hdf5"), ("", "adios2"), ("", "pdc"),
          ("Experiment scripts", "runone"), ("", "runmatrix"), ("Analysis", "plotsmeasured"),
          ("Build and patches", "builddeps"), ("", "volasyncpatch"), ("", "adiospatch")]


def find(lines, name, start, end):
    s = next((i for i, l in enumerate(lines) if re.search(start, l)), None)
    if s is None:
        sys.exit(f"code_refs: start of {name!r} not found ({start})")
    if end == "1":
        return s, s
    if end == "eof":
        e = len(lines) - 1
        while e > s and not lines[e].strip():
            e -= 1
        return s, e
    if end == "c":
        depth, seen = 0, False
        for i in range(s, len(lines)):
            depth += lines[i].count("{") - lines[i].count("}")
            seen = seen or "{" in lines[i]
            if seen and depth <= 0:
                return s, i
    if end == "py":
        e = s
        for i in range(s + 1, len(lines)):
            if lines[i].strip() and not lines[i][0].isspace():
                break
            if lines[i].strip():
                e = i
        return s, e
    if end == "sh":
        for i in range(s + 1, len(lines)):
            if lines[i].rstrip() == "}":
                return s, i
    else:
        for i in range(s, len(lines)):
            if re.search(end, lines[i]):
                return s, i
    sys.exit(f"code_refs: end of {name!r} not found ({end})")


def main():
    found = {}
    for name, lst, path, start, end, desc, sec in SEGMENTS:
        with open(os.path.join(SRC, path)) as f:
            lines = f.read().split("\n")
        a, b = find(lines, name, start, end)
        found[name] = (lst, path, a + 1, b + 1, desc, sec)

    with open(os.path.join(PAPER, "code_lines.tex"), "w") as f:
        f.write("% Generated by src/analysis/code_refs.py ; do not edit.\n")
        for name, (lst, path, a, b, _, _) in found.items():
            rng = f"{a}" if a == b else f"{a}-{b}"
            f.write(f"\\newcommand{{\\cl{name}}}{{{rng}\\xspace}}\n")
            f.write(f"\\newcommand{{\\src{name}}}{{Listing~\\ref{{lst:{lst}}}, "
                    f"line{'' if a == b else 's'}~{rng}\\xspace}}\n")

    with open(os.path.join(PAPER, "code_table.tex"), "w") as f:
        f.write("% Generated by src/analysis/code_refs.py ; do not edit.\n")
        f.write("{\\small\\setlength{\\tabcolsep}{4pt}\n\\begin{longtable}{@{}l l r p{0.42\\textwidth} l@{}}\n")
        f.write("\\caption{Code map: where each mechanism described in the paper is "
                "implemented. Line numbers refer to the listings in this appendix.}"
                "\\label{tab:codemap}\\\\\n\\toprule\n")
        hdr = "Segment & Listing & Lines & Description & Section \\\\\n\\midrule\n"
        f.write(hdr + "\\endfirsthead\n\\toprule\n" + hdr + "\\endhead\n"
                "\\bottomrule\n\\endfoot\n")
        for title, lst in GROUPS:
            rows = [(n, v) for n, v in found.items() if v[0] == lst]
            if title:
                f.write(f"\\multicolumn{{5}}{{@{{}}l}}{{\\textit{{{title}}}}} \\\\\n")
            for n, (lst_, path, a, b, desc, sec) in rows:
                rng = f"{a}" if a == b else f"{a}-{b}"
                f.write(f"\\texttt{{{n}}} & \\ref{{lst:{lst_}}} (\\texttt{{\\detokenize{{{os.path.basename(path)}}}}}) "
                        f"& {rng} & {desc} & \\S\\ref{{{sec}}} \\\\\n")
        f.write("\\end{longtable}}\n")
    print(f"code_refs: {len(found)} segments")


if __name__ == "__main__":
    main()
