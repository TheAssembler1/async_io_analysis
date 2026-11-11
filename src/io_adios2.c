/* ADIOS2 backend (BP5 engine).  One output stream; each step is an ADIOS
 * step holding a global 1-D array.  Asynchronous mode sets the BP5
 * AsyncWrite parameter (default "Guided", overridable with
 * AIO_ADIOS2_ASYNC_MODE) so that buffered data is written by a background
 * thread after EndStep; in Guided mode the compute phase is bracketed with
 * Enter/ExitComputationBlock hints.  Durability (fdatasync of every step)
 * requires the patched ADIOS2; ADIOS2_FSYNC is set here from --no-fsync. */
#define _GNU_SOURCE
#include "aio_bench.h"
#define ADIOS2_USE_MPI 1
#include <adios2_c.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    adios2_adios *adios;
    adios2_io *io;
    adios2_variable *var;
    adios2_engine *eng;
    int guided;
} bp5_state;

#define CHK(x) do { if ((x) != adios2_error_none) { \
    fprintf(stderr, "[adios2] %s failed\n", #x); MPI_Abort(c->comm, 5); } } while (0)

static int bp5_init(io_ctx *c)
{
    bp5_state *s = calloc(1, sizeof *s);
    char path[4096];
    const char *mode = getenv("AIO_ADIOS2_ASYNC_MODE");
    if (!mode) mode = "Guided";
    /* The patched FilePOSIX::Flush reads ADIOS2_FSYNC on first use. */
    setenv("ADIOS2_FSYNC", c->durable ? "1" : "0", 1);
    s->adios = adios2_init_mpi(c->comm);
    s->io = adios2_declare_io(s->adios, "out");
    CHK(adios2_set_engine(s->io, "BP5"));
    CHK(adios2_set_parameter(s->io, "AsyncWrite", c->async ? mode : "false"));
    s->guided = c->async && !strcmp(mode, "Guided");
    /* Extra BP5 parameters, AIO_ADIOS2_PARAMS="Key=Value;Key=Value". */
    const char *extra = getenv("AIO_ADIOS2_PARAMS");
    if (extra && *extra) {
        char *dup = strdup(extra), *save, *kv;
        for (kv = strtok_r(dup, ";", &save); kv; kv = strtok_r(NULL, ";", &save)) {
            char *eq = strchr(kv, '=');
            if (eq) { *eq = 0; CHK(adios2_set_parameter(s->io, kv, eq + 1)); }
        }
        free(dup);
    }
    size_t n = c->bytes / sizeof(double);
    size_t shape = n * c->nranks, start = n * c->rank, count = n;
    s->var = adios2_define_variable(s->io, "field", adios2_type_double, 1, &shape, &start, &count,
                                    adios2_constant_dims_true);
    snprintf(path, sizeof path, "%s/adios2_out.bp", c->dir);
    s->eng = adios2_open(s->io, path, adios2_mode_write);
    if (!s->eng || !s->var) return -1;
    if (c->rank == 0)
        fprintf(stderr, "[adios2] BP5 AsyncWrite=%s %s\n", c->async ? mode : "false", extra ? extra : "");
    c->priv = s;
    return 0;
}

static void bp5_issue(io_ctx *c, int step, const void *buf)
{
    bp5_state *s = c->priv;
    adios2_step_status st;
    (void)step;
    /* In async mode, BeginStep/EndStep block only if the previous step's
     * background write is still running. */
    CHK(adios2_begin_step(s->eng, adios2_step_mode_append, -1.0f, &st));
    CHK(adios2_put(s->eng, s->var, buf, adios2_mode_deferred));
    CHK(adios2_end_step(s->eng));
}

static void bp5_wait(io_ctx *c) { (void)c; /* completion is only observable at Close */ }

static void bp5_fini(io_ctx *c)
{
    bp5_state *s = c->priv;
    CHK(adios2_close(s->eng));
    adios2_finalize(s->adios);
    free(s);
}

static void bp5_cbegin(io_ctx *c)
{
    bp5_state *s = c->priv;
    if (s->guided) adios2_enter_computation_block(s->adios);
}

static void bp5_cend(io_ctx *c)
{
    bp5_state *s = c->priv;
    if (s->guided) adios2_exit_computation_block(s->adios);
}

const io_backend io_adios2 = {"adios2", bp5_init, bp5_issue, bp5_wait, bp5_fini,
                              bp5_cbegin, bp5_cend};
