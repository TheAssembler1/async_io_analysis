/* LD_PRELOAD shim for traced runs: gettimeofday() returns CLOCK_MONOTONIC
 * plus the offset to the wall clock taken when the library loads.  DFTracer
 * and src/trace.c stamp their events with gettimeofday, so both stay on one
 * clock that never steps; WSL steps the wall clock by a second or more while
 * resyncing with Windows, which reorders a trace's events otherwise.
 *
 * clock_gettime(CLOCK_REALTIME) is left alone: pthread_cond_timedwait takes
 * an absolute CLOCK_REALTIME deadline (Argobots' waiting pool computes one),
 * and a shifted clock there leaves threads asleep when they should wake.
 *
 *   cc -O2 -shared -fPIC -o libaio_monoclock.so monoclock.c */
#define _GNU_SOURCE
#include <stdint.h>
#include <sys/time.h>
#include <time.h>

static int64_t offset_ns; /* realtime - monotonic at load */

static int64_t ns(const struct timespec *t) { return (int64_t)t->tv_sec * 1000000000 + t->tv_nsec; }

__attribute__((constructor)) static void init(void)
{
    struct timespec rt, mt;
    clock_gettime(CLOCK_REALTIME, &rt);
    clock_gettime(CLOCK_MONOTONIC, &mt);
    offset_ns = ns(&rt) - ns(&mt);
}

int gettimeofday(struct timeval *restrict tv, void *restrict tz)
{
    (void)tz;
    struct timespec mt;
    clock_gettime(CLOCK_MONOTONIC, &mt);
    int64_t t = ns(&mt) + offset_ns;
    if (tv) {
        tv->tv_sec = t / 1000000000;
        tv->tv_usec = (t % 1000000000) / 1000;
    }
    return 0;
}
