/* Mercury-only model of the PDC client progress loop.
 *
 *   progress_test server <addr-file>
 *       Busy-polling Mercury server (NA_NO_BLOCK, like pdc_server). For every
 *       "write" RPC it pulls the client's buffer with HG_Bulk_transfer
 *       (HG_BULK_PULL, as PDC's transfer_request does), then responds.
 *
 *   mpiexec -n N progress_test client <addr-file>
 *       Each rank registers a buffer, then per step: forward a write RPC,
 *       run a compute kernel while the server pulls, wait for the response.
 *       A background thread drives Mercury exactly like PDC's hg_progress_fn:
 *           HG_Trigger...; HG_Progress(ctx, PT_TIMEOUT_MS); usleep(PT_SLEEP_US)
 *
 * Client environment:
 *   PT_TIMEOUT_MS   HG_Progress timeout              (PDC: 100)
 *   PT_SLEEP_US     sleep after each HG_Progress     (PDC: 1000)
 *   PT_BLOCKING     1 = do not set NA_NO_BLOCK       (PDC default: 0, busy)
 *   PT_MB           MiB per rank per step            (64)
 *   PT_STEPS        steps per phase                  (5)
 *   PT_KERNEL       sleep | spin | fma | stream      (spin)
 *   PT_TCOMP        compute seconds per step         (1.0)
 *   PT_CPUS         per-rank CPU lists, ranks separated by ';', e.g. "2;4" or "2,3;4,5"
 *   PT_HG           Mercury transport                (ofi+tcp)
 *   PT_AUTH_KEY     NA auth key, e.g. "svc_id:vni" for ofi+cxi (both sides)
 *
 *   PT_DIAG         1 = print Mercury wait-fd and progress-loop counters to stderr
 *
 * Rank 0 prints one CSV row with means over ranks (t_exposed_max: slowest rank).
 * t_wait ends when the completion callback has run; t_join is the extra time
 * to stop the progress thread, which only checks its stop flag between
 * HG_Progress calls. bg_cpu_* is the CPU time of every thread but the main one. */
#define _GNU_SOURCE
#include <errno.h>
#include <math.h>
#include <mpi.h>
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <dirent.h>

#include <mercury.h>
#include <mercury_bulk.h>
#include <mercury_proc.h>
#include <mercury_proc_bulk.h>
#include <na.h>

/* Hand-written procs: MERCURY_GEN_PROC needs Boost.Preprocessor. */
typedef struct {
    hg_bulk_t bulk;
    uint64_t  size;
    uint32_t  rank;
} write_in_t;
typedef struct {
    int32_t ret;
} write_out_t;

static hg_return_t proc_write_in_t(hg_proc_t proc, void *data)
{
    write_in_t *x = data;
    hg_return_t r = hg_proc_hg_bulk_t(proc, &x->bulk);
    if (r == HG_SUCCESS) r = hg_proc_uint64_t(proc, &x->size);
    if (r == HG_SUCCESS) r = hg_proc_uint32_t(proc, &x->rank);
    return r;
}
static hg_return_t proc_write_out_t(hg_proc_t proc, void *data)
{
    return hg_proc_int32_t(proc, &((write_out_t *)data)->ret);
}

#define CHECK(cond, msg)                                                                           \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            fprintf(stderr, "%s:%d: %s\n", __FILE__, __LINE__, msg);                               \
            exit(1);                                                                               \
        }                                                                                          \
    } while (0)

static double now(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + 1e-9 * t.tv_nsec;
}

static double thread_cpu(pthread_t th)
{
    clockid_t c;
    struct timespec t;
    if (pthread_getcpuclockid(th, &c) || clock_gettime(c, &t)) return NAN;
    return t.tv_sec + 1e-9 * t.tv_nsec;
}

static double proc_cpu(void)
{
    struct timespec t;
    clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &t);
    return t.tv_sec + 1e-9 * t.tv_nsec;
}

static const char *env_s(const char *k, const char *d)
{
    const char *v = getenv(k);
    return (v && *v) ? v : d;
}
static double env_d(const char *k, double d)
{
    const char *v = getenv(k);
    return (v && *v) ? atof(v) : d;
}

/* ------------------------------------------------------------------ server */

static char  *srv_buf[1024];
static hg_bulk_t srv_bulk[1024];
static size_t srv_size[1024];
static hg_class_t *srv_class;

struct req {
    hg_handle_t h;
    write_in_t  in;
};

static hg_return_t pull_cb(const struct hg_cb_info *ci)
{
    struct req *r = ci->arg;
    write_out_t out = {.ret = ci->ret == HG_SUCCESS ? 0 : -1};
    HG_Respond(r->h, NULL, NULL, &out);
    HG_Free_input(r->h, &r->in);
    HG_Destroy(r->h);
    free(r);
    return HG_SUCCESS;
}

static hg_return_t write_rpc(hg_handle_t h)
{
    struct req *r = calloc(1, sizeof *r);
    r->h = h;
    CHECK(HG_Get_input(h, &r->in) == HG_SUCCESS, "HG_Get_input");
    uint32_t k = r->in.rank;
    CHECK(k < 1024, "rank too large");
    if (srv_size[k] < r->in.size) {
        if (srv_buf[k]) {
            HG_Bulk_free(srv_bulk[k]);
            free(srv_buf[k]);
        }
        srv_buf[k] = malloc(r->in.size);
        memset(srv_buf[k], 0, r->in.size);
        void  *p = srv_buf[k];
        hg_size_t s = r->in.size;
        CHECK(HG_Bulk_create(srv_class, 1, &p, &s, HG_BULK_WRITE_ONLY, &srv_bulk[k]) == HG_SUCCESS,
              "HG_Bulk_create");
        srv_size[k] = r->in.size;
    }
    const struct hg_info *info = HG_Get_info(h);
    CHECK(HG_Bulk_transfer(info->context, pull_cb, r, HG_BULK_PULL, info->addr, r->in.bulk, 0,
                           srv_bulk[k], 0, r->in.size, HG_OP_ID_IGNORE) == HG_SUCCESS,
          "HG_Bulk_transfer");
    return HG_SUCCESS;
}

static int server(const char *addr_file)
{
    struct hg_init_info ii = HG_INIT_INFO_INITIALIZER;
    ii.na_init_info.progress_mode = NA_NO_BLOCK;
    ii.na_init_info.auth_key      = getenv("PT_AUTH_KEY");
    srv_class = HG_Init_opt(env_s("PT_HG", "ofi+tcp"), HG_TRUE, &ii);
    CHECK(srv_class, "HG_Init_opt (server)");
    hg_context_t *ctx = HG_Context_create(srv_class);
    HG_Register_name(srv_class, "pt_write", proc_write_in_t, proc_write_out_t, write_rpc);

    hg_addr_t self;
    char      name[256];
    hg_size_t n = sizeof name;
    HG_Addr_self(srv_class, &self);
    HG_Addr_to_string(srv_class, name, &n, self);
    HG_Addr_free(srv_class, self);
    char tmp[4096];
    snprintf(tmp, sizeof tmp, "%s.tmp", addr_file);
    FILE *f = fopen(tmp, "w");
    CHECK(f, "open addr file");
    fprintf(f, "%s\n", name);
    fclose(f);
    rename(tmp, addr_file);
    fprintf(stderr, "server listening on %s\n", name);

    for (;;) {
        unsigned int c;
        do {
            c = 0;
        } while (HG_Trigger(ctx, 0, 1, &c) == HG_SUCCESS && c);
        HG_Progress(ctx, 100);
    }
}

/* ------------------------------------------------------------------ kernels */

static double *sa, *sb, *sc;
static size_t  sn = 4u << 20; /* 3 x 32 MiB: well beyond L3 */
static volatile double sink;

static void kernel(const char *k, double units)
{
    if (!strcmp(k, "sleep")) {
        double s = units * 1e-3;
        struct timespec t = {(time_t)s, (long)((s - (time_t)s) * 1e9)};
        while (nanosleep(&t, &t) && errno == EINTR) {}
    } else if (!strcmp(k, "spin")) {
        unsigned long x = 1, n = (unsigned long)(units * 1e6);
        for (unsigned long i = 0; i < n; i++) x = x * 6364136223846793005ul + i;
        sink = (double)x;
    } else if (!strcmp(k, "fma")) {
        /* compute-bound: independent FMA chains on an L1-resident array */
        double a[512];
        for (int i = 0; i < 512; i++) a[i] = i * 1e-3;
        long n = (long)(units * 1e3);
        for (long r = 0; r < n; r++)
            for (int i = 0; i < 512; i++) a[i] = a[i] * 0.999999 + 1e-7;
        sink = a[7];
    } else if (!strcmp(k, "stream")) {
        long n = (long)units;
        for (long r = 0; r < n; r++)
            for (size_t i = 0; i < sn; i++) sa[i] = sb[i] + 3.0 * sc[i];
        sink = sa[7];
    }
}

/* ------------------------------------------------------------------ client */

static hg_class_t   *cl_class;
static hg_context_t *cl_ctx;
static volatile int  pt_stop;
static unsigned      pt_timeout_ms, pt_sleep_us;

static double    pt_cpu_total; /* CPU seconds used by all progress threads so far */
static int       pt_diag;
static long      diag_iter, diag_trywait_ok, diag_timeout, diag_success;
static pthread_t pt;

static void *progress_fn(void *arg)
{
    (void)arg;
    unsigned int c;
    while (!pt_stop) {
        do {
            c = 0;
        } while (HG_Trigger(cl_ctx, 0, 1, &c) == HG_SUCCESS && c && !pt_stop);
        if (pt_diag) {
            na_class_t   *na  = HG_Core_class_get_na(cl_class->core_class);
            na_context_t *nac = HG_Core_context_get_na(cl_ctx->core_context);
            diag_iter++;
            diag_trywait_ok += NA_Poll_try_wait(na, nac);
        }
        if (!pt_stop) {
            hg_return_t r = HG_Progress(cl_ctx, pt_timeout_ms);
            diag_timeout += r == HG_TIMEOUT;
            diag_success += r == HG_SUCCESS;
        }
        if (pt_sleep_us) usleep(pt_sleep_us);
    }
    pt_cpu_total += thread_cpu(pthread_self());
    return NULL;
}

/* PDC creates the thread at transfer start and joins it once nothing is in
 * flight (PDC_Client_transfer_pthread_create/_terminate). */
static void pt_start(void)
{
    pt_stop = 0;
    pthread_create(&pt, NULL, progress_fn, NULL); /* no attr: inherits the rank's mask */
}
static void pt_end(void)
{
    pt_stop = 1;
    pthread_join(pt, NULL);
}

struct op {
    volatile int done;
    double       t_done;
};

static hg_return_t fwd_cb(const struct hg_cb_info *ci)
{
    struct op *o = ci->arg;
    o->t_done = now();
    write_out_t out;
    if (HG_Get_output(ci->info.forward.handle, &out) == HG_SUCCESS)
        HG_Free_output(ci->info.forward.handle, &out);
    __atomic_store_n(&o->done, 1, __ATOMIC_RELEASE);
    return HG_SUCCESS;
}

static void pin(int rank)
{
    const char *s = getenv("PT_CPUS");
    if (!s || !*s) return;
    char *dup = strdup(s), *save = NULL, *grp = strtok_r(dup, ";", &save);
    for (int i = 0; grp && i < rank; i++) grp = strtok_r(NULL, ";", &save);
    CHECK(grp, "PT_CPUS has fewer groups than ranks");
    cpu_set_t set;
    CPU_ZERO(&set);
    char *s2 = NULL;
    for (char *c = strtok_r(grp, ",", &s2); c; c = strtok_r(NULL, ",", &s2)) CPU_SET(atoi(c), &set);
    CHECK(sched_setaffinity(0, sizeof set, &set) == 0, "sched_setaffinity");
    free(dup);
}

static int count_threads(void)
{
    DIR *d = opendir("/proc/self/task");
    int  n = 0;
    for (struct dirent *e; d && (e = readdir(d));)
        if (e->d_name[0] != '.') n++;
    if (d) closedir(d);
    return n;
}

static int client(const char *addr_file)
{
    int rank, np;
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &np);
    pin(rank); /* before Mercury and the progress thread exist, so they inherit it */

    const char *kname = env_s("PT_KERNEL", "spin");
    size_t      bytes = (size_t)(env_d("PT_MB", 64) * (1 << 20));
    int         steps = (int)env_d("PT_STEPS", 5);
    double      tcomp = env_d("PT_TCOMP", 1.0);
    pt_timeout_ms     = (unsigned)env_d("PT_TIMEOUT_MS", 100);
    pt_sleep_us       = (unsigned)env_d("PT_SLEEP_US", 1000);
    int blocking      = (int)env_d("PT_BLOCKING", 0);

    sa = malloc(sn * 8), sb = malloc(sn * 8), sc = malloc(sn * 8);
    for (size_t i = 0; i < sn; i++) sa[i] = 0, sb[i] = 1, sc[i] = 2;
    char *buf = malloc(bytes);
    memset(buf, rank + 1, bytes);

    /* calibrate units per second, all ranks at once (as in the real run) */
    double ups = 1e3; /* sleep: units are ms */
    if (strcmp(kname, "sleep")) {
        double u = 1;
        MPI_Barrier(MPI_COMM_WORLD);
        for (;;) {
            double t0 = now();
            kernel(kname, u);
            double dt = now() - t0;
            if (dt > 0.3) {
                ups = u / dt;
                break;
            }
            u *= dt > 0.01 ? 0.4 / dt : 10;
        }
        MPI_Allreduce(MPI_IN_PLACE, &ups, 1, MPI_DOUBLE, MPI_MIN, MPI_COMM_WORLD);
    }
    double units = tcomp * ups; /* reset below when PT_RATIO is set */

    struct hg_init_info ii = HG_INIT_INFO_INITIALIZER;
    if (!blocking) ii.na_init_info.progress_mode = NA_NO_BLOCK;
    ii.na_init_info.auth_key = getenv("PT_AUTH_KEY");
    cl_class = HG_Init_opt(env_s("PT_HG", "ofi+tcp"), HG_FALSE, &ii);
    CHECK(cl_class, "HG_Init_opt (client)");
    cl_ctx       = HG_Context_create(cl_class);
    pt_diag      = (int)env_d("PT_DIAG", 0);
    if (pt_diag && rank == 0)
        fprintf(stderr, "diag: NA poll fd = %d\n",
                NA_Poll_get_fd(HG_Core_class_get_na(cl_class->core_class),
                               HG_Core_context_get_na(cl_ctx->core_context)));
    hg_id_t wid  = HG_Register_name(cl_class, "pt_write", proc_write_in_t, proc_write_out_t, NULL);

    char  name[256] = {0};
    FILE *f         = fopen(addr_file, "r");
    CHECK(f && fgets(name, sizeof name, f), "read addr file");
    fclose(f);
    name[strcspn(name, "\n")] = 0;
    hg_addr_t srv;
    CHECK(HG_Addr_lookup2(cl_class, name, &srv) == HG_SUCCESS, "HG_Addr_lookup2");

    hg_bulk_t bulk;
    void     *p = buf;
    hg_size_t s = bytes;
    CHECK(HG_Bulk_create(cl_class, 1, &p, &s, HG_BULK_READ_ONLY, &bulk) == HG_SUCCESS,
          "HG_Bulk_create");

    double t_alone = 0, t_comp = 0, t_wait = 0, t_join = 0, t_xfer = 0, T_async = 0, T_sync = 0;
    double t_xfer_sync = 0, t_io_sync = 0, bg_sync = 0, pt_cpu_async, c0, w0, pc0, mc0, tw, tj;
    write_in_t in = {.bulk = bulk, .size = bytes, .rank = (uint32_t)rank};

#define ISSUE(o, h)                                                                                \
    do {                                                                                           \
        (o).done = 0;                                                                              \
        pt_start();                                                                                \
        HG_Create(cl_ctx, srv, wid, &(h));                                                         \
        HG_Forward((h), fwd_cb, &(o), &in);                                                        \
    } while (0)
    /* tw: until the completion callback has run; tj: joining the progress
     * thread afterwards (PDCregion_transfer_close in PDC) */
#define WAIT(o, tw, tj)                                                                            \
    do {                                                                                           \
        double a_ = now();                                                                         \
        while (!__atomic_load_n(&(o).done, __ATOMIC_ACQUIRE)) usleep(1000); /* PDC's wait loop */ \
        double b_ = now();                                                                         \
        pt_end();                                                                                  \
        (tw) = b_ - a_, (tj) = now() - b_;                                                         \
    } while (0)

    /* warm-up transfer (connection setup) */
    {
        struct op o;
        hg_handle_t h;
        ISSUE(o, h);
        WAIT(o, tw, tj);
        HG_Destroy(h);
    }

    /* PT_RATIO: compute per step = ratio x transfer time (slowest rank) */
    double ratio = env_d("PT_RATIO", 0);
    if (ratio > 0) {
        double tio = 0;
        MPI_Barrier(MPI_COMM_WORLD);
        for (int i = 0; i < 3; i++) {
            struct op o;
            hg_handle_t h;
            double t0 = now();
            ISSUE(o, h);
            WAIT(o, tw, tj);
            tio += (o.t_done - t0) / 3;
            HG_Destroy(h);
        }
        MPI_Allreduce(MPI_IN_PLACE, &tio, 1, MPI_DOUBLE, MPI_MAX, MPI_COMM_WORLD);
        tcomp = ratio * tio;
        units = tcomp * ups;
    }

    /* 1. synchronous: transfer, wait, then compute */
    MPI_Barrier(MPI_COMM_WORLD);
    w0 = now();
    for (int i = 0; i < steps; i++) {
        struct op o;
        hg_handle_t h;
        pc0 = proc_cpu(), mc0 = thread_cpu(pthread_self());
        double t0 = now();
        ISSUE(o, h);
        WAIT(o, tw, tj);
        t_io_sync += now() - t0;
        t_xfer_sync += o.t_done - t0;
        bg_sync += (proc_cpu() - pc0) - (thread_cpu(pthread_self()) - mc0);
        HG_Destroy(h);
        kernel(kname, units);
    }
    MPI_Barrier(MPI_COMM_WORLD);
    T_sync = now() - w0;

    /* 2. compute alone, no thread (as in PDC when nothing is in flight) */
    MPI_Barrier(MPI_COMM_WORLD);
    for (int i = 0; i < steps; i++) {
        double t0 = now();
        kernel(kname, units);
        t_alone += now() - t0;
    }

    /* 3. asynchronous: start transfer, compute, wait (the measured phase) */
    MPI_Barrier(MPI_COMM_WORLD);
    c0 = pt_cpu_total, w0 = now();
    pc0 = proc_cpu(), mc0 = thread_cpu(pthread_self());
    for (int i = 0; i < steps; i++) {
        struct op o;
        hg_handle_t h;
        double t0 = now();
        ISSUE(o, h);
        double t1 = now();
        kernel(kname, units);
        double t2 = now();
        WAIT(o, tw, tj);
        t_comp += t2 - t1;
        t_wait += tw;
        t_join += tj;
        t_xfer += o.t_done - t0;
        HG_Destroy(h);
    }
    MPI_Barrier(MPI_COMM_WORLD);
    T_async      = now() - w0;
    pt_cpu_async = pt_cpu_total - c0;
    /* everything but the main thread: progress thread, provider threads, MPI */
    double bg_async = (proc_cpu() - pc0) - (thread_cpu(pthread_self()) - mc0);
    int nthreads = count_threads() + 1; /* + the joined progress thread */

    double gib_rank = (double)bytes / (1 << 30);
    double v[] = {t_alone / steps, t_comp / steps, t_wait / steps, t_join / steps,
                  (t_wait + t_join) / steps, t_xfer / steps, t_xfer_sync / steps,
                  T_async, T_sync, pt_cpu_async / T_async, bg_async / T_async,
                  bg_sync / t_io_sync, bg_sync / (gib_rank * steps), nthreads};
    enum { NV = sizeof v / sizeof v[0] };
    double sum[NV], mx[NV];
    MPI_Reduce(v, sum, NV, MPI_DOUBLE, MPI_SUM, 0, MPI_COMM_WORLD);
    MPI_Reduce(v, mx, NV, MPI_DOUBLE, MPI_MAX, 0, MPI_COMM_WORLD);
    if (pt_diag && rank == 0)
        fprintf(stderr, "diag: loop iterations %ld, try_wait true %ld, HG_Progress success %ld timeout %ld\n",
                diag_iter, diag_trywait_ok, diag_success, diag_timeout);
    if (rank == 0) {
        printf("kernel,blocking,timeout_ms,sleep_us,hg,cpus,nranks,mib,steps,t_alone,t_comp,slowdown,"
               "t_wait,t_join,t_exposed_max,t_xfer,t_xfer_sync,gib_s_sync,T_async,T_sync,"
               "pt_cpu_async,bg_cpu_async,bg_cpu_xfer,bg_cpu_s_per_gib,nthreads\n");
        printf("%s,%d,%u,%u,%s,\"%s\",%d,%.0f,%d,%.4f,%.4f,%.3f,%.4f,%.4f,%.4f,%.4f,%.4f,%.3f,%.3f,"
               "%.3f,%.3f,%.3f,%.3f,%.4f,%.0f\n",
               kname, blocking, pt_timeout_ms, pt_sleep_us, env_s("PT_HG", "ofi+tcp"),
               env_s("PT_CPUS", ""), np, bytes / 1048576.0, steps, sum[0] / np, sum[1] / np,
               sum[1] / sum[0], sum[2] / np, sum[3] / np, mx[4], sum[5] / np, sum[6] / np,
               gib_rank * np / (sum[6] / np), mx[7], mx[8], sum[9] / np, sum[10] / np,
               sum[11] / np, sum[12] / np, mx[13]);
    }

    HG_Bulk_free(bulk);
    HG_Addr_free(cl_class, srv);
    HG_Context_destroy(cl_ctx);
    HG_Finalize(cl_class);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: %s server|client <addr-file>\n", argv[0]);
        return 2;
    }
    if (!strcmp(argv[1], "server")) return server(argv[2]);
    MPI_Init(&argc, &argv);
    int r = client(argv[2]);
    MPI_Finalize();
    return r;
}
