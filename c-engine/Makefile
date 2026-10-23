# Builds the C engine as build/liblux.a and build/liblux.so (the one Python's
# ctypes will load).
#
# -ffp-contract=off keeps the compiler from fusing a multiply and an add into one
# instruction, which rounds once instead of twice: the rules' few floats must
# round as Python rounds them.

CFLAGS ?= -O2
CFLAGS += -std=c11 -Wall -Wextra -Wpedantic -Wshadow -Wconversion -ffp-contract=off -fvisibility=hidden -fPIC -Iinclude

SRC := $(wildcard src/*.c)
OBJ := $(SRC:src/%.c=build/%.o)
HEADERS := include/lux.h src/internal.h src/pymath.h

all: build/liblux.a build/liblux.so

build/%.o: src/%.c $(HEADERS) | build
	$(CC) $(CFLAGS) -c $< -o $@

build/liblux.a: $(OBJ)
	$(AR) rcs $@ $^

build/liblux.so: $(OBJ)
	$(CC) -shared -o $@ $^ -lm

build:
	mkdir -p build

# the differential tester against the Python engine, with the repository's .venv
test: build/liblux.so
	PYGAME_HIDE_SUPPORT_PROMPT=1 ../.venv/bin/python -W ignore tests/scenarios.py
	PYGAME_HIDE_SUPPORT_PROMPT=1 ../.venv/bin/python -W ignore tests/difftest.py --games 20 --quiet

clean:
	rm -rf build

.PHONY: all test clean
