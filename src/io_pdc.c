/* PDC (Proactive Data Containers) backend.  Each step is a PDC object of
 * nranks*bytes; every rank transfers its region with the region-transfer
 * API.  Asynchronous mode starts the transfer and waits for it before the
 * next step; blocking mode waits immediately.  Data movement to storage is
 * performed by the PDC servers (launched separately, see scripts/run_pdc.sh).
 *
 * AIO_PDC_SHIFT=k makes rank r write the region of rank (r+k) mod nranks.
 * With the static partition each server owns a contiguous range of ranks'
 * regions, so a shift of one node's worth of ranks sends every client's
 * data to the servers on the next node instead of its own.
 *
 * Compiled only with -DHAVE_PDC (make pdc builds aio_bench_pdc against an
 * existing PDC install); without PDC the backend reports itself unavailable. */
#include "aio_bench.h"
#include <stdio.h>
#include <stdlib.h>

#ifdef HAVE_PDC
#include <pdc.h>

/* PDC after "Standardize Collective Client Calls" (#310) renamed the
 * collective create calls; build with -DPDC_COLL_API against those. */
#ifdef PDC_COLL_API
#define CONT_CREATE(name, prop, comm) PDCcont_create_coll(name, prop, comm)
#define OBJ_CREATE(cont, name, prop, comm) PDCobj_create_coll(cont, name, prop, 0, comm)
#else
#define CONT_CREATE(name, prop, comm) PDCcont_create_col(name, prop)
#define OBJ_CREATE(cont, name, prop, comm) PDCobj_create_mpi(cont, name, prop, 0, comm)
#endif

typedef struct {
    pdcid_t pdc, cont_prop, cont, obj_prop, obj, local, remote, xfer;
    int pending, shift;
} pdc_state;

static int pdc_init(io_ctx *c)
{
    pdc_state *s = calloc(1, sizeof *s);
    const char *shift = getenv("AIO_PDC_SHIFT");
    if (shift) s->shift = atoi(shift);
    uint64_t dims = (uint64_t)(c->bytes / sizeof(double)) * c->nranks;
    s->pdc = PDCinit("pdc");
    s->cont_prop = PDCprop_create(PDC_CONT_CREATE, s->pdc);
    s->cont = CONT_CREATE("aio_bench", s->cont_prop, c->comm);
    s->obj_prop = PDCprop_create(PDC_OBJ_CREATE, s->pdc);
    PDCprop_set_obj_type(s->obj_prop, PDC_DOUBLE);
    PDCprop_set_obj_dims(s->obj_prop, 1, &dims);
    PDCprop_set_obj_transfer_region_type(s->obj_prop, PDC_REGION_STATIC);
    c->priv = s;
    return 0;
}

static void pdc_wait(io_ctx *c)
{
    pdc_state *s = c->priv;
    if (!s->pending) return;
    PDCregion_transfer_wait(s->xfer);
    PDCregion_transfer_close(s->xfer);
    PDCregion_close(s->local);
    PDCregion_close(s->remote);
    PDCobj_close(s->obj);
    s->pending = 0;
}

static void pdc_issue(io_ctx *c, int step, const void *buf)
{
    pdc_state *s = c->priv;
    char name[32];
    uint64_t n = c->bytes / sizeof(double), zero = 0;
    uint64_t off = n * (uint64_t)((c->rank + s->shift % c->nranks + c->nranks) % c->nranks);
    pdc_wait(c);
    snprintf(name, sizeof name, "step%03d", step);
    s->obj = OBJ_CREATE(s->cont, name, s->obj_prop, c->comm);
    s->local = PDCregion_create(1, &zero, &n);
    s->remote = PDCregion_create(1, &off, &n);
    s->xfer = PDCregion_transfer_create((void *)buf, PDC_WRITE, s->obj, s->local, s->remote);
    PDCregion_transfer_start(s->xfer);
    s->pending = 1;
    if (!c->async) pdc_wait(c);
}

static void pdc_fini(io_ctx *c)
{
    pdc_state *s = c->priv;
    pdc_wait(c);
    /* The PDC server caches regions in memory and flushes them later; flush
     * before the timed run ends so that, as for the other backends, all data
     * has been handed to the storage layer when the run completes. */
    MPI_Barrier(c->comm);
    if (c->rank == 0) PDCobj_flush_all_start();
    MPI_Barrier(c->comm);
    PDCprop_close(s->obj_prop);
    PDCcont_close(s->cont);
    PDCprop_close(s->cont_prop);
    PDCclose(s->pdc);
    free(s);
}
#else
static int pdc_init(io_ctx *c)
{
    if (c->rank == 0) fprintf(stderr, "[pdc] aio_bench was built without PDC (-DHAVE_PDC)\n");
    return -1;
}
static void pdc_issue(io_ctx *c, int step, const void *buf) { (void)c; (void)step; (void)buf; }
static void pdc_wait(io_ctx *c) { (void)c; }
static void pdc_fini(io_ctx *c) { (void)c; }
#endif

const io_backend io_pdc = {"pdc", pdc_init, pdc_issue, pdc_wait, pdc_fini, NULL, NULL};
