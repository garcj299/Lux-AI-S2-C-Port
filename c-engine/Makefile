# Builds the C engine as build/liblux.a and build/liblux.so (the one Python's
# ctypes loads), and runs its tests against the Python engine.
#
# -ffp-contract=off keeps the compiler from fusing a multiply and an add into one
# instruction, which rounds once instead of twice: the rules' few floats must
# round as Python rounds them. The flags the port needs are in LUX_CFLAGS, after
# CFLAGS, so `make CFLAGS=-O3` changes the optimisation without dropping them.

CFLAGS ?= -O2
LUX_CFLAGS := -std=c11 -Wall -Wextra -Wpedantic -Wshadow -Wconversion -ffp-contract=off \
              -fvisibility=hidden -fPIC -Iinclude
SANITIZE := -fsanitize=address,undefined -fno-sanitize-recover=undefined -fno-omit-frame-pointer

SRC := $(wildcard src/*.c)
OBJ := $(SRC:src/%.c=build/%.o)
HEADERS := include/lux.h src/internal.h src/pymath.h

all: build/liblux.a build/liblux.so

build/%.o: src/%.c $(HEADERS) | build
	$(CC) $(CFLAGS) $(LUX_CFLAGS) -c $< -o $@

build/liblux.a: $(OBJ)
	$(AR) rcs $@ $^

build/liblux.so: $(OBJ)
	$(CC) -shared -o $@ $^ -lm

build:
	mkdir -p build

# ---- Tests, against the Python engine in the repository's .venv ----------------
#
# LIB is the library tested: `make test LIB=path/to/liblux.so` tests another
# build (tests/mutants.py and test-asan do).

LIB := build/liblux.so
RUN := PYGAME_HIDE_SUPPORT_PROMPT=1 ../.venv/bin/python -W ignore

# What 20 random games always reach (keys of tests/harness.py's Coverage). A key
# left at 0 fails the run: the games would no longer test that rule. The rarer
# cases are hand-built in tests/scenarios.py instead.
REQUIRED := move_ok move_dropped transfer_ok pickup_ok pickup_dropped dig_ok dig_dropped \
            self_destruct_ok self_destruct_dropped recharge_ok factory_build_ok \
            factory_build_dropped factory_water_ok factory_water_dropped dig_rubble dig_ice \
            dig_ore dig_bare built self_destructed crushed by_power built_under quirk3 \
            quirk4_transfer quirk4_pickup refine_ice refine_ore lichen_spread lichen_connected \
            lichen_max dry lost_all

test: test-errors test-scenarios test-games test-malformed

test-errors: $(LIB)
	$(RUN) tests/errors.py --lib $(LIB)

test-scenarios: $(LIB)
	$(RUN) tests/scenarios.py --lib $(LIB)

test-games: $(LIB)
	$(RUN) tests/difftest.py --lib $(LIB) --games 20 --quiet --require "$(REQUIRED)"

test-malformed: $(LIB)
	$(RUN) tests/difftest.py --lib $(LIB) --seed 1000 --games 40 --malformed 0.05 --quiet \
		--require "malformed raised_KeyError raised_ValueError"

# The tests again, on a build with AddressSanitizer and UBSan. Python itself isn't
# built with them, so their runtimes are preloaded.
build/asan/liblux.so: $(SRC) $(HEADERS)
	mkdir -p build/asan
	$(CC) $(CFLAGS) $(LUX_CFLAGS) $(SANITIZE) -shared -o $@ $(SRC) -lm

test-asan: build/asan/liblux.so
	LD_PRELOAD="$$($(CC) -print-file-name=libasan.so) $$($(CC) -print-file-name=libubsan.so)" \
	ASAN_OPTIONS=detect_leaks=0 $(MAKE) test LIB=build/asan/liblux.so

# Deliberately broken copies of the engine, each of which the tests must catch.
mutants: build/liblux.so
	$(RUN) tests/mutants.py

clean:
	rm -rf build

.PHONY: all test test-errors test-scenarios test-games test-malformed test-asan mutants clean
