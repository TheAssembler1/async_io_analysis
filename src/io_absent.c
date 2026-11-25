/* Stand-ins for the HDF5 and ADIOS2 backends, for PDC-only builds of aio_bench. */
#include "aio_bench.h"
#include <stdio.h>
static int absent(io_ctx *c)
{
    if (c->rank == 0) fprintf(stderr, "backend not built on this system\n");
    return -1;
}
static void nop_issue(io_ctx *c, int step, const void *buf) { (void)c; (void)step; (void)buf; }
static void nop(io_ctx *c) { (void)c; }
const io_backend io_hdf5 = {"hdf5", absent, nop_issue, nop, nop, NULL, NULL};
const io_backend io_adios2 = {"adios2", absent, nop_issue, nop, nop, NULL, NULL};
