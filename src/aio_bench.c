/* aio_bench: alternate a compute phase with an I/O phase for a number of
 * steps, using a selectable compute surrogate and I/O backend.
 *
 *   for each step:  compute(t_comp)  ->  write step (blocking or async)  ->  barrier
 *   async:          issuing step i first waits for step i-1 (one in flight)
 *
 * The compute surrogate is calibrated, with all ranks running concurrently
 * and no I/O, to a fixed amount of work whose standalone duration is
 * --tcomp seconds.  Any growth of the compute phase in the timed loop is
 * therefore interference from the I/O path.
 *
 * Output: one CSV row (rank 0) with per-step means of the max over ranks. */
#define _GNU_SOURCE
#include "aio_bench.h"
#include <getopt.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

double now(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + 1e-9 * ts.tv_nsec;
}

MPI_Info romio_hints(void)
{
    const char *env = getenv("AIO_ROMIO_HINTS");
    if (!env || !*env) return MPI_INFO_NULL;
    MPI_Info info;
    char *s = strdup(env), *save, *kv;
    MPI_Info_create(&info);
    for (kv = strtok_r(s, ",", &save); kv; kv = strtok_r(NULL, ",", &save)) {
        char *eq = strchr(kv, '=');
        if (eq) { *eq = 0; MPI_Info_set(info, kv, eq + 1); }
    }
    free(s);
    return info;
}

static const io_backend *find_backend(const char *s)
{
    const io_backend *all[] = {&io_posix, &io_mpiio, &io_hdf5, &io_adios2, &io_pdc};
    for (size_t i = 0; i < sizeof all / sizeof *all; i++)
        if (!strcmp(s, all[i]->name)) return all[i];
    return NULL;
}

static double timed_max(kernel_t k, double units, MPI_Comm comm, void *buf, size_t bytes)
{
    double t0, t, tmax;
    MPI_Barrier(comm);
    t0 = now();
    kernel_run(k, units);
    kernel_output(k, buf, bytes);
    t = now() - t0;
    MPI_Allreduce(&t, &tmax, 1, MPI_DOUBLE, MPI_MAX, comm);
    return tmax;
}

/* Find the number of work units whose concurrent standalone run takes tcomp.
 * Work is linear in units, so calibrate against a short target (at most
 * CAL_TARGET seconds) and scale; the standalone baseline measured afterwards
 * at the full length reports any residual error. */
#define CAL_TARGET 2.0
#define GPU_WARMUP 5.0
static double calibrate(kernel_t k, double tcomp, MPI_Comm comm, void *buf, size_t bytes)
{
    if (k == K_SLEEP) return tcomp * 1e6;
    /* GPUs idle at a low clock and boost only under sustained load, so a short
     * calibration burst would underestimate their speed: warm the device up
     * for GPU_WARMUP seconds and calibrate GPU kernels at the full length. */
    int gpu = (k == K_GPU_SPIN || k == K_GPU_BLOCK);
    double target = (gpu || tcomp < CAL_TARGET) ? tcomp : CAL_TARGET;
    double u = 1, t;
    timed_max(k, u, comm, buf, bytes); /* warm up */
    if (gpu)
        for (double w = 0; w < GPU_WARMUP; u *= 2) w += timed_max(k, u, comm, buf, bytes);
    while ((t = timed_max(k, u, comm, buf, bytes)) < 0.1 * target) u *= 4;
    for (int it = 0; it < 2; it++) {
        u = u * target / t;
        t = timed_max(k, u, comm, buf, bytes);
    }
    return u * target / t * (tcomp / target);
}

static void usage(void)
{
    fprintf(stderr,
            "usage: aio_bench --io {posix|mpiio|hdf5|adios2|pdc|none} --mode {sync|async}\n"
            "                 --kernel {none|sleep|spin|dgemm|stream|halo|heat|gpu-spin|gpu-block}\n"
            "                 [--tcomp s=2] [--mb MiB/rank=64] [--steps n=5] [--dir path=.]\n"
            "                 [--units u] [--tag label] [--csv file] [--no-fsync]\n"
            "                 [--alone-reps n=3]\n");
}

int main(int argc, char **argv)
{
    int provided, rank, nranks;
    MPI_Init_thread(&argc, &argv, MPI_THREAD_MULTIPLE, &provided);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &nranks);
    /* MPI file errors return silently by default; make them abort instead
     * (also covers the MPI-IO calls HDF5 makes underneath). */
    MPI_File_set_errhandler(MPI_FILE_NULL, MPI_ERRORS_ARE_FATAL);

    const char *io_name = "none", *mode = "sync", *kname = "sleep", *dir = ".", *tag = "", *csv = NULL;
    double tcomp = 2.0, units = -1, mb = 64;
    int steps = 5, durable = 1, alone_reps = 3;
    static struct option opts[] = {
        {"io", 1, 0, 'i'}, {"mode", 1, 0, 'm'}, {"kernel", 1, 0, 'k'}, {"tcomp", 1, 0, 't'},
        {"mb", 1, 0, 'b'}, {"steps", 1, 0, 's'}, {"dir", 1, 0, 'd'}, {"units", 1, 0, 'u'},
        {"tag", 1, 0, 'g'}, {"csv", 1, 0, 'c'}, {"no-fsync", 0, 0, 'n'},
        {"alone-reps", 1, 0, 'a'}, {0, 0, 0, 0}};
    for (int o; (o = getopt_long(argc, argv, "", opts, NULL)) != -1;) {
        switch (o) {
        case 'i': io_name = optarg; break;
        case 'm': mode = optarg; break;
        case 'k': kname = optarg; break;
        case 't': tcomp = atof(optarg); break;
        case 'b': mb = atof(optarg); break;
        case 's': steps = atoi(optarg); break;
        case 'd': dir = optarg; break;
        case 'u': units = atof(optarg); break;
        case 'g': tag = optarg; break;
        case 'c': csv = optarg; break;
        case 'n': durable = 0; break;
        case 'a': alone_reps = atoi(optarg); break;
        default: if (!rank) usage(); MPI_Abort(MPI_COMM_WORLD, 1);
        }
    }
    int k = kernel_parse(kname);
    const io_backend *be = strcmp(io_name, "none") ? find_backend(io_name) : NULL;
    if (k < 0 || (strcmp(io_name, "none") && !be)) { if (!rank) usage(); MPI_Abort(MPI_COMM_WORLD, 1); }
    if (provided < MPI_THREAD_MULTIPLE && !rank) fprintf(stderr, "warning: MPI_THREAD_MULTIPLE not provided\n");

    io_ctx ctx = {MPI_COMM_WORLD, rank, nranks, (size_t)(mb * 1048576.0) / 8 * 8,
                  !strcmp(mode, "async"), durable, dir, NULL};
    void *buf;
    if (posix_memalign(&buf, 4096, ctx.bytes)) MPI_Abort(MPI_COMM_WORLD, 1);
    for (size_t i = 0; i < ctx.bytes / sizeof(double); i++) ((double *)buf)[i] = rank + 1e-6 * i;
    if (!rank) mkdir(dir, 0755);

    /* ---- calibration and standalone baseline (no I/O) ---- */
    kernel_setup(k, MPI_COMM_WORLD, ctx.bytes);
    double t_alone = 0;
    if (k != K_NONE) {
        if (units < 0) units = calibrate(k, tcomp, MPI_COMM_WORLD, buf, ctx.bytes);
        MPI_Bcast(&units, 1, MPI_DOUBLE, 0, MPI_COMM_WORLD);
        for (int r = 0; r < alone_reps; r++)
            t_alone += timed_max(k, units, MPI_COMM_WORLD, buf, ctx.bytes) / alone_reps;
    }

    /* ---- timed epoch loop ---- */
    double t_init = now();
    if (be && be->init(&ctx)) {
        if (!rank) fprintf(stderr, "backend %s unavailable\n", io_name);
        MPI_Abort(MPI_COMM_WORLD, 6);
    }
    t_init = now() - t_init;

    double *tc = calloc(steps, sizeof *tc), *ti = calloc(steps, sizeof *ti);
    MPI_Barrier(MPI_COMM_WORLD);
    double t0 = now();
    for (int s = 0; s < steps; s++) {
        double a = now();
        if (be && be->compute_begin) be->compute_begin(&ctx);
        if (k != K_NONE) { kernel_run(k, units); kernel_output(k, buf, ctx.bytes); }
        if (be && be->compute_end) be->compute_end(&ctx);
        double b = now();
        if (be) be->issue(&ctx, s, buf);
        /* The I/O phase ends when every rank has finished it; without this
         * barrier, ranks that finish early would charge the imbalance to the
         * next compute phase (e.g., waiting in a halo exchange). */
        MPI_Barrier(MPI_COMM_WORLD);
        tc[s] = b - a;
        ti[s] = now() - b;
    }
    double tf = now();
    if (be) { be->wait(&ctx); be->fini(&ctx); }
    double t_end = now();
    tf = t_end - tf;
    double total = t_end - t0;

    /* ---- reduce: per-step max over ranks, then mean over steps ---- */
    double *tcm = calloc(steps, sizeof *tcm), *tim = calloc(steps, sizeof *tim);
    double io_rank = tf, io_max, tf_max, total_max;
    for (int s = 0; s < steps; s++) io_rank += ti[s];
    MPI_Reduce(tc, tcm, steps, MPI_DOUBLE, MPI_MAX, 0, MPI_COMM_WORLD);
    MPI_Reduce(ti, tim, steps, MPI_DOUBLE, MPI_MAX, 0, MPI_COMM_WORLD);
    MPI_Reduce(&io_rank, &io_max, 1, MPI_DOUBLE, MPI_MAX, 0, MPI_COMM_WORLD);
    MPI_Reduce(&tf, &tf_max, 1, MPI_DOUBLE, MPI_MAX, 0, MPI_COMM_WORLD);
    MPI_Reduce(&total, &total_max, 1, MPI_DOUBLE, MPI_MAX, 0, MPI_COMM_WORLD);

    if (!rank) {
        double comp = 0, io = 0;
        for (int s = 0; s < steps; s++) { comp += tcm[s] / steps; io += tim[s] / steps; }
        double gb = (double)ctx.bytes * nranks * steps / 1e9;
        FILE *f = csv ? fopen(csv, "a") : stdout;
        if (f && csv && ftell(f) == 0)
            fprintf(f, "io,mode,kernel,tag,durable,nranks,mib_per_rank,steps,tcomp_target,units,"
                       "t_alone,t_comp,slowdown,t_io_step,t_final,t_io_obs,t_total,t_init,gb\n");
        fprintf(f, "%s,%s,%s,%s,%d,%d,%.1f,%d,%.3f,%.6g,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.3f\n",
                io_name, mode, kname, tag, durable, nranks, mb, steps, tcomp, units, t_alone, comp,
                t_alone > 0 ? comp / t_alone : 0, io, tf_max, io_max, total_max, t_init, gb);
        if (csv) fclose(f);
        fprintf(stderr, "%s/%s/%s[%s] P=%d alone=%.3f comp=%.3f (x%.2f) io/step=%.3f final=%.3f total=%.3f\n",
                io_name, mode, kname, tag, nranks, t_alone, comp, t_alone > 0 ? comp / t_alone : 0, io,
                tf_max, total_max);
    }
    kernel_teardown(k);
    free(buf);
    MPI_Finalize();
    return 0;
}
