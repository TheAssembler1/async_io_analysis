/* POSIX backend.  Each step is one shared file; rank r writes its block at
 * offset r*bytes.  Blocking mode uses pwrite(); asynchronous mode copies the
 * block to a staging buffer and submits it with POSIX AIO (aio_write), which
 * glibc services from a background thread.  When durable, blocking mode
 * calls fdatasync() and asynchronous mode queues aio_fsync(O_DSYNC). */
#define _GNU_SOURCE
#include "aio_bench.h"
#include <aio.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef struct {
    void *stage;
    struct aiocb cb, sync_cb;
    int fd, pending;
} posix_state;

static int open_step(io_ctx *c, int step)
{
    char path[4096];
    snprintf(path, sizeof path, "%s/posix_step%03d.dat", c->dir, step);
    int fd = open(path, O_CREAT | O_WRONLY, 0644);
    if (fd < 0) { perror(path); MPI_Abort(c->comm, 3); }
    return fd;
}

static int posix_init(io_ctx *c)
{
    posix_state *s = calloc(1, sizeof *s);
    if (c->async) {
        struct aioinit ai = {.aio_threads = 1, .aio_num = 4, .aio_idle_time = 1};
        aio_init(&ai);
        if (posix_memalign(&s->stage, 4096, c->bytes)) return -1;
        memset(s->stage, 0, c->bytes);
    }
    c->priv = s;
    return 0;
}

static void posix_wait(io_ctx *c)
{
    posix_state *s = c->priv;
    if (!s->pending) return;
    const struct aiocb *list[2] = {&s->cb, c->durable ? &s->sync_cb : NULL};
    while (aio_error(&s->cb) == EINPROGRESS) aio_suspend(list, 2, NULL);
    if (aio_return(&s->cb) != (ssize_t)c->bytes) { fprintf(stderr, "aio_write failed\n"); MPI_Abort(c->comm, 3); }
    if (c->durable) {
        while (aio_error(&s->sync_cb) == EINPROGRESS) aio_suspend(list + 1, 1, NULL);
        if (aio_return(&s->sync_cb)) { fprintf(stderr, "aio_fsync failed\n"); MPI_Abort(c->comm, 3); }
    }
    close(s->fd);
    s->pending = 0;
}

static void posix_issue(io_ctx *c, int step, const void *buf)
{
    posix_state *s = c->priv;
    off_t off = (off_t)c->rank * c->bytes;
    if (!c->async) {
        int fd = open_step(c, step);
        const char *p = buf;
        for (size_t done = 0; done < c->bytes;) {
            ssize_t n = pwrite(fd, p + done, c->bytes - done, off + done);
            if (n < 0) { perror("pwrite"); MPI_Abort(c->comm, 3); }
            done += n;
        }
        if (c->durable) fdatasync(fd);
        close(fd);
        return;
    }
    posix_wait(c); /* staging buffer is single-buffered */
    memcpy(s->stage, buf, c->bytes);
    s->fd = open_step(c, step);
    memset(&s->cb, 0, sizeof s->cb);
    s->cb.aio_fildes = s->fd;
    s->cb.aio_buf = s->stage;
    s->cb.aio_nbytes = c->bytes;
    s->cb.aio_offset = off;
    if (aio_write(&s->cb)) { perror("aio_write"); MPI_Abort(c->comm, 3); }
    if (c->durable) {
        memset(&s->sync_cb, 0, sizeof s->sync_cb);
        s->sync_cb.aio_fildes = s->fd;
        if (aio_fsync(O_DSYNC, &s->sync_cb)) { perror("aio_fsync"); MPI_Abort(c->comm, 3); }
    }
    s->pending = 1;
}

static void posix_fini(io_ctx *c)
{
    posix_state *s = c->priv;
    posix_wait(c);
    free(s->stage);
    free(s);
}

const io_backend io_posix = {"posix", posix_init, posix_issue, posix_wait, posix_fini, NULL, NULL};
