# Build aio_bench against the stack installed by scripts/build_deps.sh.
#   make            CPU + GPU (needs nvcc)
#   make CUDA=0     CPU only
#   make PDC=1      also build the PDC backend (needs PDC in $(DEPS))
DEPS   ?= $(abspath ../deps/install)
CUDA   ?= 1
PDC    ?= 0
NVCC   ?= /usr/local/cuda/bin/nvcc
CC      = mpicc
CFLAGS  = -O2 -march=native -Wall -Wextra -Wno-unused-parameter -I$(DEPS)/include
LDFLAGS = -L$(DEPS)/lib -Wl,-rpath,$(DEPS)/lib
LIBS    = -lhdf5 -ladios2_c_mpi -ladios2_c -lopenblas -lrt -lpthread -lm

OBJS = aio_bench.o kernels.o io_posix.o io_mpiio.o io_hdf5.o io_adios2.o io_pdc.o
ifeq ($(CUDA),1)
  OBJS += gpu_kernels.o
  LIBS += -L/usr/local/cuda/lib64 -Wl,-rpath,/usr/local/cuda/lib64 -lcudart -lstdc++
else
  OBJS += gpu_stub.o
endif
ifeq ($(PDC),1)
  CFLAGS += -DHAVE_PDC
  LIBS   += -lpdc -lmercury
endif

aio_bench: $(OBJS)
	$(CC) -o $@ $^ $(LDFLAGS) $(LIBS)

# PDC-enabled build as a separate binary, against an existing PDC install
# (see scripts/pdc_env.sh).  make pdc PDC_DIR=... MERCURY_DIR=...
PDC_DIR     ?= /mnt/fast/nlewis/workspace/install/pdc
MERCURY_DIR ?= /mnt/fast/nlewis/workspace/install/mercury
PDC_OBJS = $(filter-out io_pdc.o,$(OBJS)) io_pdc_on.o
io_pdc_on.o: io_pdc.c aio_bench.h
	$(CC) $(CFLAGS) -DHAVE_PDC -I$(PDC_DIR)/include -I$(MERCURY_DIR)/include -c $< -o $@
pdc: $(PDC_OBJS)
	$(CC) -o aio_bench_pdc $^ $(LDFLAGS) $(LIBS) -Wl,--disable-new-dtags -L$(PDC_DIR)/lib -Wl,-rpath,$(PDC_DIR)/lib \
	  -L$(MERCURY_DIR)/lib -Wl,-rpath,$(MERCURY_DIR)/lib -lpdc -lpdc_commons -lmercury

%.o: %.c aio_bench.h
	$(CC) $(CFLAGS) -c $<

gpu_kernels.o: gpu_kernels.cu
	$(NVCC) -O2 -arch=native -c $< -o $@

clean:
	rm -f *.o aio_bench aio_bench_pdc

.PHONY: clean pdc
