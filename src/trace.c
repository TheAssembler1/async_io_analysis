/* Tracing and thread placement for aio_bench; each part is off unless its
 * variable is set.
 *
 *   AIO_TRACE=prefix  write the main thread's phases (compute, each HDF5 call,
 *                     barriers) to prefix.<rank>.phases.csv at exit.
 *   AIO_SAMPLE_MS=n   with AIO_TRACE, sample every thread of the process every
 *                     n ms (CPU it is on, run time, run-queue wait from
 *                     /proc/self/task/<tid>/schedstat) to prefix.<rank>.threads.csv.
 *                     Run-queue wait of the main thread during compute is the
 *                     time something else held its CPU.
 *   AIO_PIN=iocore    main thread on the first CPU of the rank's mask, every
 *                     other thread (async VOL execution stream, MPI or
 *                     libfabric helpers, the sampler) on the remaining CPUs.
 *
 * Everything is kept in memory and written at the end, so tracing does no
 * I/O during the run.  Times are microseconds since the epoch from
 * gettimeofday, the clock DFTracer stamps its events with.  The realtime clock can be
 * stepped during a run (WSL resyncs it with Windows by a second or more), so
 * each phase is converted with the offset current when it is recorded. */
#define _GNU_SOURCE
#include "aio_bench.h"
#include <dirent.h>
#include <pthread.h>
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

static const char *prefix;
static int trank;
typedef struct { const char *name; int step; double t0, t1; } phase_t; /* t0, t1: epoch us */
static phase_t *phases;
static size_t nphases, cap_phases;

typedef struct { double ts; int tid, cpu; unsigned long long run_ns, wait_ns; } sample_t;
static sample_t *samples;
static size_t nsamples, cap_samples;
static pthread_t sampler;
static volatile int sampling;
static int sampler_tid, sample_ms;

/* wall clock (gettimeofday, as DFTracer reads it) - CLOCK_MONOTONIC now, seconds */
static double epoch_offset(void)
{
    struct timeval rt;
    struct timespec mt;
    gettimeofday(&rt, NULL);
    clock_gettime(CLOCK_MONOTONIC, &mt);
    return (rt.tv_sec - mt.tv_sec) + 1e-6 * rt.tv_usec - 1e-9 * mt.tv_nsec;
}

static void *grow(void *p, size_t *cap, size_t need, size_t size)
{
    if (need <= *cap) return p;
    *cap = *cap ? 2 * *cap : 4096;
    if (!(p = realloc(p, *cap * size))) { perror("aio_bench trace"); MPI_Abort(MPI_COMM_WORLD, 1); }
    return p;
}

void trace_phase(const char *name, int step, double t0, double t1)
{
    if (!prefix) return;
    phases = grow(phases, &cap_phases, nphases + 1, sizeof *phases);
    double off = epoch_offset();
    phases[nphases++] = (phase_t){name, step, (t0 + off) * 1e6, (t1 + off) * 1e6};
}

/* Fields 14-15 (utime, stime) and 39 (processor) of /proc/<tid>/stat. */
static int read_stat(int tid, int *cpu, unsigned long long *ticks)
{
    char path[64], buf[1024], *p;
    snprintf(path, sizeof path, "/proc/self/task/%d/stat", tid);
    FILE *f = fopen(path, "r");
    if (!f) return -1;
    size_t n = fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    buf[n] = 0;
    if (!(p = strrchr(buf, ')'))) return -1;
    unsigned long long ut = 0, st = 0;
    int field = 2;
    for (char *tok = strtok(p + 1, " "); tok; tok = strtok(NULL, " ")) {
        field++;
        if (field == 14) ut = strtoull(tok, NULL, 10);
        else if (field == 15) st = strtoull(tok, NULL, 10);
        else if (field == 39) { *cpu = atoi(tok); break; }
    }
    *ticks = ut + st;
    return 0;
}

static void sample_once(void)
{
    static long hz;
    if (!hz) hz = sysconf(_SC_CLK_TCK);
    DIR *d = opendir("/proc/self/task");
    if (!d) return;
    struct timeval rt;
    gettimeofday(&rt, NULL);
    double ts = rt.tv_sec * 1e6 + rt.tv_usec;
    for (struct dirent *e; (e = readdir(d));) {
        if (e->d_name[0] == '.') continue;
        int tid = atoi(e->d_name), cpu = -1;
        unsigned long long run = 0, wait = 0, ticks = 0;
        char path[64];
        snprintf(path, sizeof path, "/proc/self/task/%d/schedstat", tid);
        FILE *f = fopen(path, "r");
        if (f) {
            if (fscanf(f, "%llu %llu", &run, &wait) != 2) run = wait = 0;
            fclose(f);
        }
        if (read_stat(tid, &cpu, &ticks)) continue;
        /* Kernels without schedstats (WSL) report zeros: fall back to
         * utime+stime, which has clock-tick resolution and no wait time. */
        if (!run) run = ticks * (1000000000ULL / hz);
        samples = grow(samples, &cap_samples, nsamples + 1, sizeof *samples);
        samples[nsamples++] = (sample_t){ts, tid, cpu, run, wait};
    }
    closedir(d);
}

static void *sampler_main(void *arg)
{
    (void)arg;
    sampler_tid = syscall(SYS_gettid);
    struct timespec next;
    clock_gettime(CLOCK_MONOTONIC, &next);
    while (sampling) {
        sample_once();
        next.tv_nsec += sample_ms * 1000000L;
        while (next.tv_nsec >= 1000000000L) { next.tv_nsec -= 1000000000L; next.tv_sec++; }
        clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL);
    }
    sample_once();
    return NULL;
}

void trace_init(int rank)
{
    trank = rank;
    const char *p = getenv("AIO_TRACE");
    if (!p || !*p) return;
    prefix = p;
    const char *ms = getenv("AIO_SAMPLE_MS");
    sample_ms = ms ? atoi(ms) : 0;
    if (sample_ms > 0) {
        sampling = 1;
        pthread_create(&sampler, NULL, sampler_main, NULL);
    }
}

static void cpus_str(int tid, char *out, size_t len)
{
    cpu_set_t s;
    size_t o = 0;
    out[0] = 0;
    if (sched_getaffinity(tid, sizeof s, &s)) return;
    for (int c = 0; c < CPU_SETSIZE && o + 8 < len; c++)
        if (CPU_ISSET(c, &s)) o += snprintf(out + o, len - o, "%s%d", o ? " " : "", c);
}

static void thread_comm(int tid, char *out, size_t len)
{
    char path[64];
    snprintf(path, sizeof path, "/proc/self/task/%d/comm", tid);
    FILE *f = fopen(path, "r");
    out[0] = 0;
    if (f) {
        if (fgets(out, len, f)) out[strcspn(out, "\n")] = 0;
        fclose(f);
    }
}

void trace_fini(void)
{
    if (!prefix) return;
    if (sampling) { sampling = 0; pthread_join(sampler, NULL); }
    char path[4096], comm[64], cpus[1024];
    snprintf(path, sizeof path, "%s.%d.phases.csv", prefix, trank);
    FILE *f = fopen(path, "w");
    if (f) {
        fprintf(f, "rank,pid,name,step,ts_us,dur_us\n");
        for (size_t i = 0; i < nphases; i++)
            fprintf(f, "%d,%d,%s,%d,%.1f,%.1f\n", trank, (int)getpid(), phases[i].name, phases[i].step,
                    phases[i].t0, phases[i].t1 - phases[i].t0);
        fclose(f);
    }
    if (!sample_ms) return;
    snprintf(path, sizeof path, "%s.%d.threads.csv", prefix, trank);
    if (!(f = fopen(path, "w"))) return;
    /* Thread roles and affinity as of the end of the run; tids that exited
     * before then (the sampler, by now) appear only in the samples. */
    fprintf(f, "# tid=%d role=sampler\n", sampler_tid);
    DIR *d = opendir("/proc/self/task");
    for (struct dirent *e; d && (e = readdir(d));) {
        if (e->d_name[0] == '.') continue;
        int tid = atoi(e->d_name);
        thread_comm(tid, comm, sizeof comm);
        cpus_str(tid, cpus, sizeof cpus);
        fprintf(f, "# tid=%d role=%s comm=%s cpus=%s\n", tid, tid == getpid() ? "main" : "other", comm, cpus);
    }
    if (d) closedir(d);
    fprintf(f, "rank,pid,ts_us,tid,cpu,run_ns,wait_ns\n");
    for (size_t i = 0; i < nsamples; i++)
        fprintf(f, "%d,%d,%.1f,%d,%d,%llu,%llu\n", trank, (int)getpid(), samples[i].ts, samples[i].tid,
                samples[i].cpu, samples[i].run_ns, samples[i].wait_ns);
    fclose(f);
}

/* Call from the main thread.  The first call remembers the rank's mask;
 * later calls also move threads created since then (the async VOL creates
 * its execution stream when HDF5 initializes), which inherit the main
 * thread's single CPU. */
void pin_threads(void)
{
    static cpu_set_t orig;
    static int have;
    const char *mode = getenv("AIO_PIN");
    if (!mode || strcmp(mode, "iocore")) return;
    if (!have) { sched_getaffinity(0, sizeof orig, &orig); have = 1; }
    cpu_set_t first, rest = orig;
    int c0 = -1;
    for (int c = 0; c < CPU_SETSIZE && c0 < 0; c++)
        if (CPU_ISSET(c, &orig)) c0 = c;
    CPU_ZERO(&first);
    CPU_SET(c0, &first);
    CPU_CLR(c0, &rest);
    if (!CPU_COUNT(&rest)) {
        if (trank == 0) fprintf(stderr, "AIO_PIN=iocore needs at least 2 CPUs per rank, leaving threads as they are\n");
        return;
    }
    sched_setaffinity(0, sizeof first, &first);
    DIR *d = opendir("/proc/self/task");
    for (struct dirent *e; d && (e = readdir(d));) {
        int tid = atoi(e->d_name);
        if (tid > 0 && tid != getpid()) sched_setaffinity(tid, sizeof rest, &rest);
    }
    if (d) closedir(d);
    if (trank == 0 && getenv("AIO_DEBUG")) {
        char cpus[1024];
        cpus_str(0, cpus, sizeof cpus);
        fprintf(stderr, "[pin] rank 0 main on CPU %s, other threads on the rest\n", cpus);
    }
}
