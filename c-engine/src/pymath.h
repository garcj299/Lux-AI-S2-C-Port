/*
 * pymath.h: the few places where Python arithmetic differs from C's.
 *
 * Python's // and % round toward minus infinity; C's / and % toward zero.
 * Python's slice(start, stop) clamps out-of-range bounds instead of reading past
 * the array. And the rules' few floats must be computed as Python computes them:
 * IEEE doubles, one rounding per operation, no fused multiply-add (the Makefile
 * passes -ffp-contract=off) and no wider intermediate precision (checked below).
 */
#ifndef LUX_PYMATH_H
#define LUX_PYMATH_H

#include <float.h>
#include <math.h>
#include <stdint.h>

_Static_assert(FLT_EVAL_METHOD == 0,
               "doubles must be evaluated as plain doubles (x86-64 SSE2, not x87), as in Python");

/* a // b for b != 0 */
static inline int64_t py_floordiv(int64_t a, int64_t b)
{
    int64_t q = a / b;
    if ((a % b != 0) && ((a < 0) != (b < 0))) {
        q -= 1;
    }
    return q;
}

/* a % b for b != 0: the result has the sign of b */
static inline int64_t py_mod(int64_t a, int64_t b)
{
    int64_t r = a % b;
    if (r != 0 && ((r < 0) != (b < 0))) {
        r += b;
    }
    return r;
}

static inline int64_t py_min(int64_t a, int64_t b)
{
    return b < a ? b : a;
}

static inline int64_t py_max(int64_t a, int64_t b)
{
    return b > a ? b : a;
}

/* NumPy's clip(lo, hi) of one value: min(max(v, lo), hi), so hi wins when hi < lo */
static inline int64_t py_clip(int64_t v, int64_t lo, int64_t hi)
{
    return py_min(py_max(v, lo), hi);
}

/* math.floor and math.ceil of a double, as ints */
static inline int64_t py_floor(double x)
{
    return (int64_t)floor(x);
}

static inline int64_t py_ceil(double x)
{
    return (int64_t)ceil(x);
}

/* The indices that slice(start, stop) selects on an axis of length n, as the
 * half-open range [*lo, *hi). Step 1, as every slice in the engine. */
static inline void py_slice(int64_t start, int64_t stop, int64_t n, int64_t *lo, int64_t *hi)
{
    if (start < 0) {
        start += n;
        if (start < 0) {
            start = 0;
        }
    } else if (start > n) {
        start = n;
    }
    if (stop < 0) {
        stop += n;
        if (stop < 0) {
            stop = 0;
        }
    } else if (stop > n) {
        stop = n;
    }
    *lo = start;
    *hi = stop < start ? start : stop;
}

#endif /* LUX_PYMATH_H */
