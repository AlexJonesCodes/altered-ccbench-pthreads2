# Paths
SRC     = src
INCLUDE = include

# Compiler and flags
CC      = gcc
CFLAGS  = -O3 -Wall
LDFLAGS = -lm -lrt
VER_FLAGS = -D_GNU_SOURCE

# Enable DEBUG build with: make VERSION=DEBUG
ifeq ($(VERSION),DEBUG)
CFLAGS  = -O0 -ggdb -Wall -g -fno-inline
endif

# Threads
CFLAGS  += -pthread
LDFLAGS += -pthread

# Host/platform detection
UNAME ?= $(shell uname -n)

ifeq ($(UNAME), lpd48core)
  PLATFORM = OPTERON
  CC = gcc
  PLATFORM_NUMA = 1
endif

ifeq ($(UNAME), diassrv8)
  PLATFORM = XEON
  CC = gcc
  PLATFORM_NUMA = 1
endif

ifeq ($(UNAME), maglite)
  PLATFORM = NIAGARA
  CC = /opt/csw/bin/gcc
  CFLAGS += -m64 -mcpu=v9 -mtune=v9
endif

ifeq ($(UNAME), parsasrv1.epfl.ch)
  PLATFORM = TILERA
  CC = tile-gcc
  LDFLAGS += -ltmc
endif

ifeq ($(UNAME), diascld19)
  PLATFORM = XEON2
  CC = gcc
endif

ifeq ($(UNAME), diascld9)
  PLATFORM = OPTERON2
  CC = gcc
endif

ifeq ($(UNAME), RYZEN53600)
  PLATFORM = RYZEN53600
  CC = gcc
endif

ifeq ($(UNAME), i3_7020U)
  PLATFORM = i3_7020U
  CC = gcc
endif

ifeq ($(UNAME), i9_13900HX)
  PLATFORM = I9_13900HX
  CC = gcc
endif

ifeq ($(PLATFORM),)
  PLATFORM = DEFAULT
  CC = gcc
endif

VER_FLAGS += -D$(PLATFORM)

# NUMA support: default ON. Disable with `make NUMA=0`.
NUMA ?= 1

# Backward-compat: if PLATFORM_NUMA=1 is set by hostname rules, force NUMA=1
ifeq ($(PLATFORM_NUMA),1)
  NUMA := 1
endif

ifeq ($(NUMA),1)
  LDFLAGS  += -lnuma
  VER_FLAGS += -DPLATFORM_NUMA
endif

# Includes
INCFLAGS = -I./$(INCLUDE)

# Objects
OBJS = ccbench.o pfd.o barrier.o

# Phonies
.PHONY: all default clean

default: ccbench
all: ccbench

# Link
ccbench: $(OBJS)
	$(CC) $(VER_FLAGS) -o $@ $(OBJS) $(CFLAGS) $(LDFLAGS) $(INCFLAGS)

# Compile objects
ccbench.o: $(SRC)/ccbench.c $(INCLUDE)/ccbench.h $(INCLUDE)/common.h $(INCLUDE)/barrier.h $(INCLUDE)/pfd.h
	$(CC) $(VER_FLAGS) -c $(SRC)/ccbench.c $(CFLAGS) $(INCFLAGS)

pfd.o: $(SRC)/pfd.c $(INCLUDE)/pfd.h $(INCLUDE)/common.h
	$(CC) $(VER_FLAGS) -c $(SRC)/pfd.c $(CFLAGS) $(INCFLAGS)

barrier.o: $(SRC)/barrier.c $(INCLUDE)/barrier.h $(INCLUDE)/common.h
	$(CC) $(VER_FLAGS) -c $(SRC)/barrier.c $(CFLAGS) $(INCFLAGS)

clean:
	rm -f *.o ccbench
