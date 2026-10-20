# vec-noise 1.1.4, patched locally

`luxai_s2` uses this library (simplex noise) to generate maps. This is the original source as published on PyPI, with one change so it builds against NumPy 2.

- **Source:** `vec_noise-1.1.4.zip` from PyPI, uploaded 2017-08-02, sha256 `3f86a7fb1dc7fb2b489066f9b9e5902da7bb727f727460d524eca16e8a6c844e`. Upstream: https://github.com/zbenjamin/vec_noise
- **License:** MIT, see `LICENSE.txt`.

## What was changed

In `_simplex.c` only, the local variables `I`, `J`, `K` were renamed to `I_`, `J_`, `K_`. Nothing else was touched.

Why: NumPy 2's C headers include `<complex.h>`, which defines a macro `I` (the imaginary unit), so `int I, J;` no longer compiles. The rename doesn't change behaviour.

## Building it

`pip install` of this directory fails: `setup.py` sets `__builtins__.__NUMPY_SETUP__`, and in pip's isolated build `__builtins__` is a dict. Build it the old way instead, from a copy so the build output stays out of the repository, with the environment from `requirements.lock` already installed (it provides numpy, setuptools and wheel):

    cp -r third_party/vec-noise /tmp/vec-noise-build
    cd /tmp/vec-noise-build && <repo>/.venv/bin/python setup.py -q bdist_wheel
    <repo>/.venv/bin/pip install dist/vec_noise-1.1.4-*.whl
