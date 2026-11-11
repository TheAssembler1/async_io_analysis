// GPU compute surrogate: an FMA-bound kernel that keeps the device busy while
// the host either spin-waits (CUDA's default for few contexts per core) or
// blocks in the driver (cudaDeviceScheduleBlockingSync).  The output field
// lives on the device and is copied to the host before every I/O phase.
#include <cuda_runtime.h>
#include <stdio.h>
#include <stdlib.h>

#define CK(x) do { cudaError_t e = (x); if (e != cudaSuccess) { \
    fprintf(stderr, "CUDA %s:%d %s\n", __FILE__, __LINE__, cudaGetErrorString(e)); abort(); } } while (0)

static float *d_field;
static size_t d_n;
static cudaStream_t stream;

__global__ void fma_kernel(float *x, size_t n, int iters)
{
    for (size_t i = blockIdx.x * (size_t)blockDim.x + threadIdx.x; i < n;
         i += (size_t)gridDim.x * blockDim.x) {
        float a = x[i], b = 1.0001f, c = 0.9999f;
        for (int k = 0; k < iters; k++) { a = fmaf(a, b, c); b = fmaf(b, c, a) * 1e-3f; }
        x[i] = a + b;
    }
}

extern "C" int gpu_available(void)
{
    int n = 0;
    return cudaGetDeviceCount(&n) == cudaSuccess && n > 0;
}

extern "C" void gpu_setup(int blocking_sync, size_t bytes)
{
    CK(cudaSetDeviceFlags(blocking_sync ? cudaDeviceScheduleBlockingSync : cudaDeviceScheduleSpin));
    CK(cudaSetDevice(0));
    d_n = bytes / sizeof(float);
    CK(cudaMalloc(&d_field, d_n * sizeof(float)));
    CK(cudaMemset(d_field, 0, d_n * sizeof(float)));
    CK(cudaStreamCreate(&stream));
}

// One unit = one launch over a 1M-element slice with 4096 FMA pairs per element.
extern "C" void gpu_run(double units)
{
    long n = (long)(units + 0.5);
    size_t slice = d_n < (1u << 20) ? d_n : (1u << 20);
    for (long i = 0; i < n; i++)
        fma_kernel<<<1024, 256, 0, stream>>>(d_field + (i * slice) % (d_n - slice + 1), slice, 4096);
    CK(cudaStreamSynchronize(stream));
}

extern "C" void gpu_output(void *out, size_t bytes)
{
    CK(cudaMemcpyAsync(out, d_field, bytes, cudaMemcpyDeviceToHost, stream));
    CK(cudaStreamSynchronize(stream));
}

extern "C" void gpu_teardown(void)
{
    cudaFree(d_field);
    cudaStreamDestroy(stream);
}
