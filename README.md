# async_io_analysis

Benchmarks and results for the asynchronous I/O interference study. Code is
shared. Everything a machine produced, and the scripts that ran it there, sit
in a directory named after that machine.

| machine | what ran there |
|---|---|
| `local` | The paper's workstation: one node, 2x Xeon Gold 5418Y. All backends (POSIX, MPI-IO, HDF5, ADIOS2, PDC). |
| `laptop` | WSL laptop: the Mercury-only progress-loop sweep (`progress_test`). |
| `perlmutter` | NERSC Perlmutter CPU nodes over Slingshot (ofi+cxi): PDC at 1 to 8 nodes, plus `progress_test`. |
| `tuolumne` | LLNL Tuolumne: build scripts only so far. |

    src/                      aio_bench (kernels, I/O backends) and Makefile
    src/progress_test/        Mercury-only model of PDC's progress thread
    scripts/<machine>/        build, run and job scripts
    scripts/common/pdc.sh     PDC checkout and build used by every machine's build script
    results/<machine>/        raw CSVs and logs
    analysis/<machine>/       scripts that turn results/<machine> into tables and figures
    analysis/                 machine-independent: code_refs.py, draw_loop.py, placeholder_plots.py,
                              models.py (async run time models checked against all machines' runs)
    figures/<machine>/        figures, written by analysis/<machine>
    figures/diagrams/         drawings not tied to a machine
    figures/placeholder/      watermarked dummy figures from placeholder_plots.py
    paper/                    LaTeX; uses figures/local
    slides/

A new machine gets the same three directories: `scripts/<name>`,
`results/<name>` and `analysis/<name>`, with figures going to `figures/<name>`.

## PDC source

Every machine builds PDC from `deps/src/pdc` (git-ignored). The first build
clones the in-flight transformations branch with the Perlmutter CXI fix
(`PDC_REPO`/`PDC_REF` in `scripts/common/pdc.sh` pick another). Later builds
leave that checkout alone, so edit it and rerun the build, e.g.
`scripts/perlmutter/build.sh pdc bench`; `CLEAN=1` rebuilds from scratch.
