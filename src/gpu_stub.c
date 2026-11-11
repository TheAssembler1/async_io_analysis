/* Stubs used when aio_bench is built without CUDA. */
#include "aio_bench.h"

int  gpu_available(void) { return 0; }
void gpu_setup(int blocking_sync, size_t bytes) { (void)blocking_sync; (void)bytes; }
void gpu_run(double units) { (void)units; }
void gpu_output(void *out, size_t bytes) { (void)out; (void)bytes; }
void gpu_teardown(void) {}
