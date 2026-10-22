"""lux: play Lux AI S2 matches and watch them in the browser.

    lux web [SEED]               play starter bot vs starter bot, then open the replay in the browser
    lux web --replay [N | PATH]  open a saved replay without playing (N=1 is the newest in replays/)
    lux list [--all]             list the saved replays, newest last

Run it through scripts/lux, which picks this repository's .venv; scripts/install.sh links that as ~/.local/bin/lux.
"""
import argparse
import asyncio
import http.server
import json
import os
import random
import sys
import threading
import warnings
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit

REPO = Path(__file__).resolve().parent.parent
REPLAYS_DIR = REPO / "replays"
VIEWER_DIR = REPO / "third_party" / "Lux-Design-S2" / "lux-eye-s2" / "dist"
# Relative to REPO, as when the runner is started by hand: the viewer shows it as the team name.
STARTER_BOT = "third_party/Lux-Design-S2/kits/python/main.py"
MAX_RANDOM_SEED = 999_999  # short enough to type again
LIST_LIMIT = 10
VIEWER_TIMEOUT = 60  # seconds the browser gets to load the replay


class LuxError(Exception):
    """A problem worth a one-line message instead of a traceback."""


def replay_name(when, seed):
    return f"{when:%Y-%m-%d_%H-%M-%S}_seed{seed}.json"


def saved_replays(replays_dir):
    """The replays in replays_dir, oldest first (the names start with the date)."""
    return sorted(Path(replays_dir).glob("*.json"))


def resolve_replay(arg, replays_dir):
    """A number counts back from the newest saved replay (1 = newest); anything else is a path."""
    if arg.isdigit():
        replays = saved_replays(replays_dir)
        number = int(arg)
        if not 1 <= number <= len(replays):
            raise LuxError(f"there is no replay {number}: {replays_dir} holds {len(replays)} replays (see: lux list)")
        return replays[-number]
    path = Path(arg)
    if not path.is_file():
        raise LuxError(f"no such replay file: {path}")
    return path


def replay_summary(path):
    """(seed, final turn) of a replay saved by the runner; '?' for what it doesn't hold."""
    data = json.loads(Path(path).read_text())
    seed = data.get("metadata", {}).get("seed", "?")
    observations = data.get("observations") or [{}]
    return seed, observations[-1].get("real_env_steps", "?")


def format_list(replays_dir, show_all=False):
    replays = saved_replays(replays_dir)
    if not replays:
        return f"No replays in {replays_dir} yet. Play one with: lux web"
    shown = replays if show_all else replays[-LIST_LIMIT:]
    lines = []
    if len(shown) < len(replays):
        lines.append(f"... {len(replays) - len(shown)} older replays not shown (lux list --all)")
    for number, path in zip(range(len(shown), 0, -1), shown):
        try:
            played = datetime.strptime(path.name[:19], "%Y-%m-%d_%H-%M-%S")
        except ValueError:  # not named by lux web
            played = datetime.fromtimestamp(path.stat().st_mtime)
        seed, turn = replay_summary(path)
        lines.append(f"{number:>4}  {played:%Y-%m-%d %H:%M}  seed {seed:<7}  {turn:>4} turns")
    return "\n".join(lines)


def play(seed, replay):
    """Play starter bot vs starter bot through the official runner, as
    `luxai-s2 BOT BOT -s SEED -v 1 -o REPLAY` would, save the replay and return the final scores."""
    # Imported here: they take a second, and only playing needs them.
    # luxai_s2 imports pygame, which was built from source here and warns that it lacks AVX2 blitting.
    warnings.filterwarnings("ignore", message="Your system is avx2 capable", category=RuntimeWarning)
    import numpy as np
    from luxai_runner.episode import Episode, EpisodeConfig, ReplayConfig
    from luxai_s2 import LuxAI_S2

    # The runner starts Python bots with the `python` on PATH: make it this environment's, as activating it would.
    os.environ["PATH"] = os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")
    os.chdir(REPO)
    if seed:  # luxai_runner/cli.py does the same, skipping seed 0
        np.random.seed(seed)
    cfg = EpisodeConfig(
        players=[STARTER_BOT, STARTER_BOT],
        env_cls=LuxAI_S2,
        seed=seed,
        env_cfg=dict(verbose=1, validate_action_space=True, max_episode_length=1000),
        verbosity=1,
        save_replay_path=str(replay),
        replay_options=ReplayConfig(save_format="json", compressed_obs=True),
        render=False,
    )
    return asyncio.run(Episode(cfg=cfg).run())


def outcome(scores):
    a, b = int(scores["player_0"]), int(scores["player_1"])
    if a == b:
        return "draw"
    return f"{'player_0' if a > b else 'player_1'} wins"


def show(replay, viewer_dir=VIEWER_DIR, open_browser=webbrowser.open, timeout=VIEWER_TIMEOUT):
    """Serve the viewer and one replay on a free local port and open them in the browser.

    lux-eye-s2 loads the replay named in `#/?input=` and needs nothing else from the server, so
    serving stops as soon as the replay has been sent, or after `timeout` seconds.
    Returns whether the browser loaded the replay.
    """
    if not (viewer_dir / "index.html").is_file():
        raise LuxError(f"the viewer is not built in {viewer_dir}; see third_party/Lux-Design-S2/LOCAL-PATCH.md")
    replay_sent = threading.Event()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(viewer_dir), **kwargs)

        def do_GET(self):
            if urlsplit(self.path).path != "/replay.json":
                return super().do_GET()
            data = Path(replay).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            replay_sent.set()

        def log_message(self, *args):
            pass

    # A thread per connection, so a browser's idle pre-opened connection can't hold up the rest.
    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}/"
        open_browser(f"{base}#/?{urlencode({'input': base + 'replay.json'})}")
        loaded = replay_sent.wait(timeout)
        server.shutdown()
    return loaded


def open_in_browser(url):
    print(f"Viewer: {url}")
    return webbrowser.open(url)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="lux", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    web = commands.add_parser("web", help="play a match and watch it in the browser, or watch a saved one")
    web.add_argument("seed", nargs="?", type=int, help=f"map seed (default: random, 0-{MAX_RANDOM_SEED})")
    web.add_argument("--replay", nargs="?", const="1", metavar="N|PATH",
                     help="watch a saved replay instead of playing: N counts back from the newest in replays/ "
                          "(default 1, the newest), anything else is a file path")
    listing = commands.add_parser("list", help="list the saved replays, newest last")
    listing.add_argument("--all", action="store_true", help=f"show every replay, not just the {LIST_LIMIT} newest")
    args = parser.parse_args(argv)

    try:
        if args.command == "list":
            print(format_list(REPLAYS_DIR, show_all=args.all))
            return 0
        if args.replay is not None:
            if args.seed is not None:
                web.error("give a seed to play, or --replay to watch a saved replay, not both")
            replay = resolve_replay(args.replay, REPLAYS_DIR)
        else:
            seed = args.seed if args.seed is not None else random.randint(0, MAX_RANDOM_SEED)
            replay = REPLAYS_DIR / replay_name(datetime.now(), seed)
            print(f"Playing seed {seed}...")
            scores = play(seed, replay)
            _, turn = replay_summary(replay)
            points = ", ".join(f"{player} {int(score)}" for player, score in scores.items())
            print(f"Seed {seed}: game over at turn {turn}. Scores: {points} ({outcome(scores)}).")
            print(f"Replay: {replay}")
        if not show(replay, open_browser=open_in_browser):
            raise LuxError(f"the browser did not load the replay within {VIEWER_TIMEOUT} s; "
                           f"try again with: lux web --replay {replay}")
    except LuxError as error:
        print(f"lux: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
