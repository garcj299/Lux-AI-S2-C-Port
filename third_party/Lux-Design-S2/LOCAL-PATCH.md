# Parts of the official Lux-Design-S2 repository

- **Source:** https://github.com/Lux-AI-Challenge/Lux-Design-S2, tag `v3.0.1`, commit `11e7e51b87a2d2a2f5e6bf4d538f9510fee5588c` (GitHub tarball of the tag).
- **License:** Apache-2.0, see `LICENSE` (copied from the repository root; `lux-eye-s2/` also has its own `LICENSE`).
- **Taken:** only `kits/python/` (the Python starter bot) and `lux-eye-s2/` (the replay viewer's source), unchanged. The engine (`luxai_s2/`, which includes `luxai_runner/`) is not copied: it is installed from PyPI (`luxai-s2==3.0.1`), and its source in this tag is identical to that release.

## Local additions

### `patches/luxai_runner-python311.patch`
The match runner (`luxai-s2` command) doesn't run on Python 3.11 or newer. Two one-line fixes in `luxai_runner/episode.py`:
- `replay_options: ReplayConfig = ReplayConfig()`: Python 3.11+ rejects a dataclass instance as a field default. Changed to `dataclasses.field(default_factory=ReplayConfig)`.
- `asyncio.wait(start_tasks, ...)`: Python 3.11+ no longer accepts bare coroutines there. Each one is now wrapped with `asyncio.ensure_future`.

Neither touches the game rules. Apply it to the installed runner after installing the environment:

    patch -p1 -d .venv/lib/python3.14/site-packages < third_party/Lux-Design-S2/patches/luxai_runner-python311.patch

Known, not fixed: the runner's `--render` opens no window, because it creates the engine with the default `render_mode="rgb_array"`. Use `scripts/watch_live.py` for a live window.

### `lux-eye-s2/dist/`
The viewer, built locally so it works offline. Upstream's `lux-eye-s2/.gitignore` ignores `dist`, so git doesn't track it. To rebuild (needs internet once, for its JavaScript dependencies), from a copy outside the repository so `node_modules` (~300 MB) stays out:

    cp -r third_party/Lux-Design-S2/lux-eye-s2 /tmp/lux-eye-build && cd /tmp/lux-eye-build
    npx --yes yarn@1.22.22 install --frozen-lockfile
    npx --yes yarn@1.22.22 build --base=./
    cp dist/* <repo>/third_party/Lux-Design-S2/lux-eye-s2/dist/

Gotcha: `yarn install` also runs the package's `prepublish` script, which builds `dist/` pointing every file at unpkg.com (internet). The explicit `build --base=./` afterwards replaces it with relative paths.

To use it: `python3 -m http.server 8000 -d third_party/Lux-Design-S2/lux-eye-s2/dist`, open http://localhost:8000, and load a replay JSON file. Browsers won't run it straight from `file://`. Loading from Kaggle or a URL needs internet; loading a file doesn't.
