/* MPI-IO backend.  One shared file per step, collective writes at rank
 * offsets.  Asynchronous mode uses MPI_File_iwrite_at_all on a staging copy;
 * whether it progresses in the background depends on the MPI library (for
 * MPICH, MPIR_CVAR_ASYNC_PROGRESS=1 starts a progress thread). */
#include "aio_bench.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    void *stage;
    MPI_File fh;
    MPI_Request req;
    int pending;
} mpiio_state;

static MPI_File open_step(io_ctx *c, int step)
{
    char path[4096];
    MPI_File fh;
    snprintf(path, sizeof path, "%s/mpiio_step%03d.dat", c->dir, step);
    if (MPI_File_open(c->comm, path, MPI_MODE_CREATE | MPI_MODE_WRONLY, romio_hints(), &fh)) {
        fprintf(stderr, "MPI_File_open %s failed\n", path);
        MPI_Abort(c->comm, 3);
    }
    if (step == 0 && c->rank == 0 && getenv("AIO_DEBUG")) {
        MPI_Info info;
        char key[MPI_MAX_INFO_KEY + 1], val[256];
        int n, flag;
        MPI_File_get_info(fh, &info);
        MPI_Info_get_nkeys(info, &n);
        for (int i = 0; i < n; i++) {
            MPI_Info_get_nthkey(info, i, key);
            MPI_Info_get(info, key, sizeof val - 1, val, &flag);
            fprintf(stderr, "[mpiio] %s=%s\n", key, val);
        }
        MPI_Info_free(&info);
    }
    return fh;
}

static int mpiio_init(io_ctx *c)
{
    mpiio_state *s = calloc(1, sizeof *s);
    if (c->async) { s->stage = malloc(c->bytes); memset(s->stage, 0, c->bytes); }
    c->priv = s;
    return 0;
}

static void mpiio_wait(io_ctx *c)
{
    mpiio_state *s = c->priv;
    if (!s->pending) return;
    MPI_Wait(&s->req, MPI_STATUS_IGNORE);
    if (c->durable) MPI_File_sync(s->fh); /* MPI has no non-blocking sync */
    MPI_File_close(&s->fh);
    s->pending = 0;
}

static void mpiio_issue(io_ctx *c, int step, const void *buf)
{
    mpiio_state *s = c->priv;
    MPI_Offset off = (MPI_Offset)c->rank * c->bytes;
    int count = (int)(c->bytes / sizeof(double));
    if (!c->async) {
        MPI_File fh = open_step(c, step);
        MPI_File_write_at_all(fh, off, buf, count, MPI_DOUBLE, MPI_STATUS_IGNORE);
        if (c->durable) MPI_File_sync(fh);
        MPI_File_close(&fh);
        return;
    }
    mpiio_wait(c);
    memcpy(s->stage, buf, c->bytes);
    s->fh = open_step(c, step);
    MPI_File_iwrite_at_all(s->fh, off, s->stage, count, MPI_DOUBLE, &s->req);
    s->pending = 1;
}

static void mpiio_fini(io_ctx *c)
{
    mpiio_state *s = c->priv;
    mpiio_wait(c);
    free(s->stage);
    free(s);
}

const io_backend io_mpiio = {"mpiio", mpiio_init, mpiio_issue, mpiio_wait, mpiio_fini, NULL, NULL};
