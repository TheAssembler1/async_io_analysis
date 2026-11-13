# Progress-loop knobs (Mercury only)

A stand-alone model of the PDC client's progress thread, without PDC. A server
pulls each client's buffer with `HG_Bulk_transfer(HG_BULK_PULL)` over
`ofi+tcp`, as the PDC server does for a region transfer. Each client starts a
progress thread at transfer start, runs a compute kernel, waits, and joins the
thread, as `PDC_Client_transfer_pthread_create`/`_terminate` do. The thread runs
the same loop as PDC's `hg_progress_fn`:

    HG_Trigger(...) until empty; HG_Progress(ctx, PT_TIMEOUT_MS); usleep(PT_SLEEP_US)

`PT_BLOCKING=1` leaves out `NA_NO_BLOCK`, so `HG_Progress` may wait in epoll
instead of spinning. PDC's defaults are timeout 100 ms, sleep 1000 us, busy.
The server always busy-polls on its own core, like `pdc_server`.

With `ofi+tcp` (libfabric 1.18 `tcp;ofi_rxm`) blocking mode does not help:
Mercury gets a wait fd and `fi_trywait` succeeds, but the thread is still
runnable almost all the time, so the epoll wait keeps returning at once
(`PT_DIAG=1` prints the counters). With `ofi+sockets` it blocks, and the
provider's own progress thread moves the data instead. `na+sm` is not built
in the workspace Mercury.

When the transfer is done, the progress thread is still inside
`HG_Progress` and only sees its stop flag when that returns, so joining it
(`PDCregion_transfer_close` in PDC) takes up to the timeout. The test reports
this as `t_join`, separate from `t_wait`.

    make                      # needs Mercury (default: ~/src/workspace/install/mercury)
    ./run_sweep.sh results.csv
    ./table.py results.csv > results.md

Each run reports, per rank: compute time alone and with a transfer in flight,
wait and join time, transfer time (forward to callback), and CPU time of the
progress thread and of all non-main threads (both include kernel time of
their socket calls). See the comment at the top of `progress_test.c` for all
variables.

The default placement pins each client to one hardware thread (`2;4;6;8`), so
the progress thread shares it with the compute; `smt` gives each client both
hardware threads of its core. The CPU numbering is what WSL reports, which may
not match the physical SMT pairs of the host CPU.
