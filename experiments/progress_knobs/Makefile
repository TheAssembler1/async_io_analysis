# Mercury from the PDC workspace; override with make MERCURY_DIR=...
MERCURY_DIR ?= $(HOME)/src/workspace/install/mercury
CC      = mpicc
CFLAGS  = -O2 -march=native -Wall -Wextra -Wno-unused-parameter -I$(MERCURY_DIR)/include
LDFLAGS = -L$(MERCURY_DIR)/lib -Wl,-rpath,$(MERCURY_DIR)/lib
LIBS    = -lmercury -lna -lmercury_util -lpthread -lm

progress_test: progress_test.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS) $(LIBS)

clean:
	rm -f progress_test
