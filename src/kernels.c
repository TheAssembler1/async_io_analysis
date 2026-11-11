/* Compute surrogates.  Each kernel performs a fixed amount of work per unit,
 * so that interference shows up as a longer compute phase (a time-based
 * busy-wait or sleep cannot be slowed down). */
#define _GNU_SOURCE
#include "aio_bench.h"
#include <cblas.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

const char *kernel_names[K_NKERNELS] = {
    "none", "sleep", "spin", "dgemm", "stream", "halo", "heat", "gpu-spin", "gpu-block"};

int kernel_parse(const char *s)
{
    for (int k = 0; k < K_NKERNELS; k++)
        if (!strcmp(s, kernel_names[k])) return k;
    return -1;
}

/* ---- spin ---- */
static volatile uint64_t spin_sink;

static void run_spin(double units)
{
    uint64_t x = 88172645463325252ULL, n = (uint64_t)(units * 1e6);
    for (uint64_t i = 0; i < n; i++) { /* xorshift: register-only ALU work */
        x ^= x << 13; x ^= x >> 7; x ^= x << 17;
    }
    spin_sink = x;
}

/* ---- dgemm: N=256 matrices (1.5 MiB total) stay in the private L2 ---- */
#define GEMM_N 256
static double *ga, *gb, *gc;

static void run_dgemm(double units)
{
    long n = (long)(units + 0.5);
    for (long i = 0; i < n; i++)
        cblas_dgemm(CblasRowMajor, CblasNoTrans, CblasNoTrans, GEMM_N, GEMM_N, GEMM_N,
                    1.0, ga, GEMM_N, gb, GEMM_N, 0.5, gc, GEMM_N);
}

/* ---- STREAM triad: 3 x 32 MiB arrays per rank, far beyond the LLC share ---- */
#define STREAM_N (4L << 20)
static double *sa, *sb, *sc;

static void run_stream(double units)
{
    long n = (long)(units + 0.5);
    const double s = 3.0;
    for (long r = 0; r < n; r++) {
        for (long i = 0; i < STREAM_N; i++) sa[i] = sb[i] + s * sc[i];
        __asm__ volatile("" ::: "memory");
    }
}

/* ---- 3-D Cartesian neighbours shared by halo and heat ---- */
static MPI_Comm cart;
static int nbr[6]; /* -x +x -y +y -z +z */

static void setup_cart(MPI_Comm comm)
{
    int n, dims[3] = {0, 0, 0}, periods[3] = {1, 1, 1};
    MPI_Comm_size(comm, &n);
    MPI_Dims_create(n, 3, dims);
    MPI_Cart_create(comm, 3, dims, periods, 0, &cart);
    for (int d = 0; d < 3; d++) MPI_Cart_shift(cart, d, 1, &nbr[2 * d], &nbr[2 * d + 1]);
}

/* ---- halo: 6 faces of 256x256 doubles (512 KiB each) + 8-byte allreduce ---- */
#define HALO_FACE (256 * 256)
static double *hsend, *hrecv;

static void exchange(double *send, double *recv, int count)
{
    MPI_Request rq[12];
    for (int f = 0; f < 6; f++) {
        MPI_Irecv(recv + (size_t)f * count, count, MPI_DOUBLE, nbr[f], f ^ 1, cart, &rq[f]);
        MPI_Isend(send + (size_t)f * count, count, MPI_DOUBLE, nbr[f], f, cart, &rq[6 + f]);
    }
    MPI_Waitall(12, rq, MPI_STATUSES_IGNORE);
}

static void run_halo(double units)
{
    long n = (long)(units + 0.5);
    for (long i = 0; i < n; i++) {
        exchange(hsend, hrecv, HALO_FACE);
        double v = hrecv[i % HALO_FACE], s;
        MPI_Allreduce(&v, &s, 1, MPI_DOUBLE, MPI_SUM, cart);
        hsend[0] = s * 1e-9;
    }
}

/* ---- heat: 7-point Jacobi on a 128^3 block with ghost cells ---- */
#define HN 128
#define HS (HN + 2)
#define IDX(i, j, k) (((size_t)(i) * HS + (j)) * HS + (k))
static double *u0, *u1, *fsend, *frecv;

static void heat_halo(double *u)
{
    const int F = HN * HN;
    for (int a = 0; a < HN; a++)
        for (int b = 0; b < HN; b++) {
            int o = a * HN + b;
            fsend[0 * F + o] = u[IDX(1, a + 1, b + 1)];
            fsend[1 * F + o] = u[IDX(HN, a + 1, b + 1)];
            fsend[2 * F + o] = u[IDX(a + 1, 1, b + 1)];
            fsend[3 * F + o] = u[IDX(a + 1, HN, b + 1)];
            fsend[4 * F + o] = u[IDX(a + 1, b + 1, 1)];
            fsend[5 * F + o] = u[IDX(a + 1, b + 1, HN)];
        }
    exchange(fsend, frecv, F);
    for (int a = 0; a < HN; a++)
        for (int b = 0; b < HN; b++) {
            int o = a * HN + b;
            u[IDX(0, a + 1, b + 1)]      = frecv[0 * F + o];
            u[IDX(HN + 1, a + 1, b + 1)] = frecv[1 * F + o];
            u[IDX(a + 1, 0, b + 1)]      = frecv[2 * F + o];
            u[IDX(a + 1, HN + 1, b + 1)] = frecv[3 * F + o];
            u[IDX(a + 1, b + 1, 0)]      = frecv[4 * F + o];
            u[IDX(a + 1, b + 1, HN + 1)] = frecv[5 * F + o];
        }
}

static void run_heat(double units)
{
    long n = (long)(units + 0.5);
    const double c = 1.0 / 7.0;
    for (long it = 0; it < n; it++) {
        heat_halo(u0);
        for (int i = 1; i <= HN; i++)
            for (int j = 1; j <= HN; j++)
                for (int k = 1; k <= HN; k++)
                    u1[IDX(i, j, k)] = c * (u0[IDX(i, j, k)] + u0[IDX(i - 1, j, k)] +
                                            u0[IDX(i + 1, j, k)] + u0[IDX(i, j - 1, k)] +
                                            u0[IDX(i, j + 1, k)] + u0[IDX(i, j, k - 1)] +
                                            u0[IDX(i, j, k + 1)]);
        double *t = u0; u0 = u1; u1 = t;
    }
}

static double *xalloc(size_t n)
{
    double *p;
    if (posix_memalign((void **)&p, 64, n * sizeof(double))) MPI_Abort(MPI_COMM_WORLD, 1);
    for (size_t i = 0; i < n; i++) p[i] = 1.0 + (double)(i % 97) * 1e-3;
    return p;
}

void kernel_setup(kernel_t k, MPI_Comm comm, size_t io_bytes)
{
    switch (k) {
    case K_DGEMM:
        ga = xalloc(GEMM_N * GEMM_N); gb = xalloc(GEMM_N * GEMM_N); gc = xalloc(GEMM_N * GEMM_N);
        break;
    case K_STREAM:
        sa = xalloc(STREAM_N); sb = xalloc(STREAM_N); sc = xalloc(STREAM_N);
        break;
    case K_HALO:
        setup_cart(comm);
        hsend = xalloc(6 * HALO_FACE); hrecv = xalloc(6 * HALO_FACE);
        break;
    case K_HEAT:
        setup_cart(comm);
        u0 = xalloc((size_t)HS * HS * HS); u1 = xalloc((size_t)HS * HS * HS);
        fsend = xalloc(6 * HN * HN); frecv = xalloc(6 * HN * HN);
        break;
    case K_GPU_SPIN:
    case K_GPU_BLOCK:
        if (!gpu_available()) {
            fprintf(stderr, "aio_bench: built without CUDA or no GPU found\n");
            MPI_Abort(MPI_COMM_WORLD, 2);
        }
        gpu_setup(k == K_GPU_BLOCK, io_bytes);
        break;
    default:
        break;
    }
}

void kernel_run(kernel_t k, double units)
{
    switch (k) {
    case K_SLEEP: {
        struct timespec ts = {(time_t)(units / 1e6), (long)(((uint64_t)units % 1000000) * 1000)};
        while (nanosleep(&ts, &ts)) ;
        break;
    }
    case K_SPIN:   run_spin(units); break;
    case K_DGEMM:  run_dgemm(units); break;
    case K_STREAM: run_stream(units); break;
    case K_HALO:   run_halo(units); break;
    case K_HEAT:   run_heat(units); break;
    case K_GPU_SPIN:
    case K_GPU_BLOCK: gpu_run(units); break;
    default: break;
    }
}

void kernel_output(kernel_t k, void *out, size_t bytes)
{
    if (k == K_GPU_SPIN || k == K_GPU_BLOCK) gpu_output(out, bytes);
    else ((volatile char *)out)[0] ^= 1; /* host data is already in place */
}

void kernel_teardown(kernel_t k)
{
    if (k == K_GPU_SPIN || k == K_GPU_BLOCK) gpu_teardown();
}
