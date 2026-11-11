/* aio_bench: compute/I-O epoch benchmark for evaluating asynchronous I/O
 * under different compute surrogates.  Shared declarations. */
#ifndef AIO_BENCH_H
#define AIO_BENCH_H

#include <mpi.h>
#include <stddef.h>
#include <stdint.h>

double now(void);
/* MPI_Info with ROMIO hints from AIO_ROMIO_HINTS="key=val,key=val" (or MPI_INFO_NULL). */
MPI_Info romio_hints(void);

/* ------------------------------------------------------------------ I/O -- */
typedef struct io_ctx {
    MPI_Comm comm;
    int rank, nranks;
    size_t bytes;      /* bytes written per rank per step */
    int async;         /* 0 = blocking, 1 = asynchronous */
    int durable;       /* 1 = a step completes only when its data is on the device */
    const char *dir;   /* output directory */
    void *priv;        /* backend state */
} io_ctx;

typedef struct io_backend {
    const char *name;
    int  (*init)(io_ctx *c);
    /* Write one step.  Blocking backends return when the data is written;
     * asynchronous backends may return as soon as `buf` can be reused. */
    void (*issue)(io_ctx *c, int step, const void *buf);
    /* Wait for all outstanding operations issued so far. */
    void (*wait)(io_ctx *c);
    void (*fini)(io_ctx *c);
    /* Optional hints bracketing the compute phase (ADIOS2 "Guided" mode). */
    void (*compute_begin)(io_ctx *c);
    void (*compute_end)(io_ctx *c);
} io_backend;

extern const io_backend io_posix, io_mpiio, io_hdf5, io_adios2, io_pdc;

/* -------------------------------------------------------------- kernels -- */
typedef enum {
    K_NONE,      /* no compute: I/O only */
    K_SLEEP,     /* nanosleep: yields core, no resources */
    K_SPIN,      /* integer ALU loop: one core, no memory traffic */
    K_DGEMM,     /* cache-resident DGEMM: CPU + caches */
    K_STREAM,    /* STREAM triad: memory bandwidth */
    K_HALO,      /* 3-D halo exchange + allreduce: MPI / interconnect */
    K_HEAT,      /* 3-D Jacobi stencil + halo exchange: mini-app */
    K_GPU_SPIN,  /* GPU FMA kernel, host spin-waits (CUDA default) */
    K_GPU_BLOCK, /* GPU FMA kernel, host blocks in the driver */
    K_NKERNELS
} kernel_t;

extern const char *kernel_names[K_NKERNELS];

int    kernel_parse(const char *s);
void   kernel_setup(kernel_t k, MPI_Comm comm, size_t io_bytes);
/* Run `units` of work of kernel k.  For K_SLEEP, units is microseconds. */
void   kernel_run(kernel_t k, double units);
/* Make the latest results available in host buffer `out` (GPU: D2H copy). */
void   kernel_output(kernel_t k, void *out, size_t bytes);
void   kernel_teardown(kernel_t k);

/* GPU entry points (gpu_kernels.cu, or stubs when built without CUDA). */
int  gpu_available(void);
void gpu_setup(int blocking_sync, size_t bytes);
void gpu_run(double units);
void gpu_output(void *out, size_t bytes);
void gpu_teardown(void);

#endif
