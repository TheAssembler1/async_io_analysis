/* HDF5 backend (parallel HDF5, MPI-IO VFD).  One file for the run; each step
 * writes a new dataset "stepNNN" of nranks*bytes with a collective write.
 * Both modes use the *_async API with an event set: under the native VOL
 * these calls complete synchronously; when the asynchronous VOL connector is
 * selected (HDF5_VOL_CONNECTOR="async under_vol=0;under_info={}") they are
 * queued and executed by its Argobots background thread.  When durable,
 * each step ends with H5Fflush, which the MPI-IO VFD turns into
 * MPI_File_sync (fsync); with flush_end (--fsync-end), no step does and every
 * rank fsyncs the file once after closing it. */
#include "aio_bench.h"
#include <hdf5.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <unistd.h>

typedef struct {
    hid_t file, fapl, dxpl, es;
    char path[4096];
} hdf5_state;

static int hdf5_init(io_ctx *c)
{
    hdf5_state *s = calloc(1, sizeof *s);
    char *path = s->path;
    snprintf(path, sizeof s->path, "%s/hdf5_out.h5", c->dir);
    s->es = H5EScreate();
    s->fapl = H5Pcreate(H5P_FILE_ACCESS);
    H5Pset_fapl_mpio(s->fapl, c->comm, romio_hints());
    /* AIO_HDF5_INDEP=1: independent raw-data I/O and metadata writes. */
    int indep = getenv("AIO_HDF5_INDEP") && atoi(getenv("AIO_HDF5_INDEP"));
    H5Pset_coll_metadata_write(s->fapl, !indep);
    s->dxpl = H5Pcreate(H5P_DATASET_XFER);
    H5Pset_dxpl_mpio(s->dxpl, indep ? H5FD_MPIO_INDEPENDENT : H5FD_MPIO_COLLECTIVE);
    s->file = H5Fcreate_async(path, H5F_ACC_TRUNC, H5P_DEFAULT, s->fapl, s->es);
    if (s->file < 0) return -1;
    /* AIO_HDF5_START=1: H5VL_async_start() from the loaded async VOL. By
     * default the async VOL hands queued tasks to its thread only while the
     * application is in a blocking call (H5ESwait, a flush, ...), and whether
     * a finished task passes on the next one depends on the same switch;
     * after this call every task goes to the thread as soon as its
     * dependencies allow. */
    if (c->async && getenv("AIO_HDF5_START") && atoi(getenv("AIO_HDF5_START"))) {
        void *lib = dlopen("libh5async.so", RTLD_NOW | RTLD_NOLOAD);
        herr_t (*start)(void) = lib ? (herr_t (*)(void))dlsym(lib, "H5VL_async_start") : NULL;
        if (!start) {
            if (c->rank == 0) fprintf(stderr, "[hdf5] AIO_HDF5_START: H5VL_async_start not found\n");
            return -1;
        }
        start();
    }
    if (c->rank == 0) {
        char name[64] = "";
        H5VLget_connector_name(s->file, name, sizeof name);
        fprintf(stderr, "[hdf5] VOL connector: %s (async=%d)\n", name, c->async);
        if (c->async && strcmp(name, "async")) fprintf(stderr, "[hdf5] WARNING: async VOL not active\n");
    }
    c->priv = s;
    return 0;
}

static void hdf5_wait(io_ctx *c)
{
    hdf5_state *s = c->priv;
    size_t ninprog;
    hbool_t failed;
    H5ESwait(s->es, H5ES_WAIT_FOREVER, &ninprog, &failed);
    if (failed) { fprintf(stderr, "[hdf5] async operation failed\n"); MPI_Abort(c->comm, 4); }
}

static void hdf5_issue(io_ctx *c, int step, const void *buf)
{
    hdf5_state *s = c->priv;
    char name[32];
    hsize_t n = c->bytes / sizeof(double), dims = n * c->nranks, start = n * c->rank;
    double t[6];
    snprintf(name, sizeof name, "step%03d", step);
    t[0] = now();
    if (c->async) hdf5_wait(c); /* at most one step in flight, as in h5bench */
    t[1] = now();
    hid_t fspace = H5Screate_simple(1, &dims, NULL);
    hid_t mspace = H5Screate_simple(1, &n, NULL);
    hid_t dset = H5Dcreate_async(s->file, name, H5T_NATIVE_DOUBLE, fspace, H5P_DEFAULT,
                                 H5P_DEFAULT, H5P_DEFAULT, s->es);
    t[2] = now();
    H5Sselect_hyperslab(fspace, H5S_SELECT_SET, &start, NULL, &n, NULL);
    H5Dwrite_async(dset, H5T_NATIVE_DOUBLE, mspace, fspace, s->dxpl, buf, s->es);
    t[3] = now();
    H5Dclose_async(dset, s->es);
    t[4] = now();
    if (c->durable) H5Fflush_async(s->file, H5F_SCOPE_LOCAL, s->es);
    H5Sclose(mspace);
    H5Sclose(fspace);
    t[5] = now();
    if (!c->async) hdf5_wait(c);
    double t6 = now();
    if (c->async) trace_phase("h5.wait", step, t[0], t[1]);
    trace_phase("h5.dcreate", step, t[1], t[2]);
    trace_phase("h5.dwrite", step, t[2], t[3]);
    trace_phase("h5.dclose", step, t[3], t[4]);
    if (c->durable) trace_phase("h5.flush", step, t[4], t[5]);
    if (!c->async) trace_phase("h5.wait", step, t[5], t6);
    if (c->rank == 0 && getenv("AIO_DEBUG"))
        fprintf(stderr, "[hdf5] step %d wait=%.3f create=%.3f write=%.3f close=%.3f flush=%.3f end=%.3f\n",
                step, t[1] - t[0], t[2] - t[1], t[3] - t[2], t[4] - t[3], t[5] - t[4], t6 - t[5]);
}

static void hdf5_fini(io_ctx *c)
{
    hdf5_state *s = c->priv;
    double t0 = now();
    hdf5_wait(c);
    double t1 = now();
    trace_phase("h5.wait", -1, t0, t1);
    H5Fclose_async(s->file, s->es);
    double t2 = now();
    hdf5_wait(c);
    double t3 = now();
    trace_phase("h5.fclose", -1, t1, t2);
    trace_phase("h5.wait", -1, t2, t3);
    /* --fsync-end: the run's only flush, an fsync of the closed file on every
     * rank. Not H5Fflush: the async VOL runs a flush as a collective task,
     * and right after H5ESwait one rank's often never starts (hang in the
     * metadata cache's barrier). */
    if (c->flush_end) {
        int fd = open(s->path, O_WRONLY);
        if (fd < 0 || fsync(fd)) { perror("aio_bench: fsync-end"); MPI_Abort(c->comm, 5); }
        close(fd);
        MPI_Barrier(c->comm);
        trace_phase("final.fsync", -1, t3, now());
    }
    if (c->rank == 0 && getenv("AIO_DEBUG"))
        fprintf(stderr, "[hdf5] fini drain=%.3f fclose=%.3f wait=%.3f\n", t1 - t0, t2 - t1, t3 - t2);
    H5ESclose(s->es);
    H5Pclose(s->dxpl);
    H5Pclose(s->fapl);
    free(s);
}

const io_backend io_hdf5 = {"hdf5", hdf5_init, hdf5_issue, hdf5_wait, hdf5_fini, NULL, NULL};
