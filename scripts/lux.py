"""lux: play Lux AI S2 matches and watch them.

    lux web [SEED]               play starter bot vs starter bot, then open the replay in the browser
    lux web --replay [N | PATH]  open a saved replay in the browser without playing: N counts back
                                 from the newest in replays/ (1, the default, is the newest);
                                 anything else is a file path
    lux live [SEED] [--delay S]  play starter bot vs starter bot in the engine's window, with the
                                 turn and each team's factories and lichen above the board. It
                                 pauses S seconds after drawing each turn (default 0.05; 0 = no
                                 pause; drawing itself takes ~0.04 s). Close the window with its
                                 X, Esc or Q; closing it mid-game stops the game unsaved
    lux list [--all]             list the saved replays, newest last (the 10 newest, or all)

SEED is the map seed, and the same seed plays the same game in web and live. Without one, a random
seed from 0 to 999999 is used and shown. Every finished game is saved in replays/ and opens again
with lux web --replay.

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
import time
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
LIVE_DELAY = 0.05  # seconds lux live pauses after drawing each turn
# lux live's band above the board. Team colors are the engine's (luxai_s2/pyvisual/visualizer.py).
BAND_HEIGHT = 60  # two lines: the teams stack on the right
BAND_FONT_SIZE = 32
BAND_BACKGROUND = (240, 236, 228)
BAND_TEXT = (51, 56, 68)
TEAM_COLORS = {"player_0": (224, 65, 40), "player_1": (0, 112, 81)}
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


def engine():
    """The engine's environment class, imported on demand: it takes a second, and only playing needs it."""
    # luxai_s2 imports pygame, which was built from source here and warns that it lacks AVX2 blitting.
    warnings.filterwarnings("ignore", message="Your system is avx2 capable", category=RuntimeWarning)
    from luxai_s2 import LuxAI_S2

    return LuxAI_S2


def play(seed, replay):
    return run_episode(seed, replay, engine())


def live(seed, replay, delay):
    """Like play, drawing every turn in the engine's pygame window and pausing `delay` seconds after each.

    Returns the final scores with the window still on the final board (see wait_until_closed).
    Closing the window (or Esc, Q) mid-game ends the game there: the runner then stops the bots
    as usual, and the unfinished replay is deleted.
    """
    base = engine()  # before pygame: it silences pygame's AVX2 warning
    import pygame

    try:
        pygame.display.init()
    except pygame.error as error:
        raise LuxError(f"no display to open the game window on ({error})")

    class LiveEnv(base):
        closed = False

        def reset(self, **kwargs):
            result = super().reset(**kwargs)
            self.scores = {}
            self.draw()
            return result

        def step(self, actions):
            obs, rewards, terminations, truncations, infos = super().step(actions)
            self.scores = rewards
            self.draw()
            if LiveEnv.closed:
                terminations = dict.fromkeys(terminations, True)
            return obs, rewards, terminations, truncations, infos

        def draw(self):
            # The engine's own board drawing, in a window taller by the band.
            if self._init_render():
                width, height = self.py_visualizer.WINDOW_SIZE
                self.screen = pygame.display.set_mode((width, height + BAND_HEIGHT))
                self.font = pygame.font.Font(None, BAND_FONT_SIZE)
            self.py_visualizer.update_scene(self.state)
            self.screen.blit(self.py_visualizer.surf, (0, BAND_HEIGHT))
            self.screen.fill(BAND_BACKGROUND, (0, 0, self.screen.get_width(), BAND_HEIGHT))
            turn = self.state.real_env_steps
            factories = {player: len(self.state.factories.get(player, {})) for player in TEAM_COLORS}
            game, *teams = band_text(seed, turn, factories, self.scores)
            text = self.font.render(game, True, BAND_TEXT)
            self.screen.blit(text, (12, (BAND_HEIGHT - text.get_height()) // 2))
            # The teams' column starts at a fixed place, so it doesn't shift as the turn number grows.
            column = 12 + self.font.size("seed 999999 · turn 1000")[0] + 40
            for row, (line, color) in enumerate(zip(teams, TEAM_COLORS.values())):
                text = self.font.render(line, True, color)
                self.screen.blit(text, (column, BAND_HEIGHT // 2 * row + (BAND_HEIGHT // 2 - text.get_height()) // 2))
            pygame.display.update()
            pygame.display.set_caption(live_title(seed, turn))
            LiveEnv.closed = LiveEnv.closed or close_requested(pygame.event.get())
            time.sleep(delay)

    scores = run_episode(seed, replay, LiveEnv)
    if LiveEnv.closed:
        pygame.quit()
        Path(replay).unlink(missing_ok=True)
        raise LuxError("the game window was closed mid-game; the game stopped and its replay was not saved")
    return scores


def close_requested(events):
    import pygame

    return any(e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key in (pygame.K_ESCAPE, pygame.K_q))
               for e in events)


def wait_until_closed():
    """Keep the game window on screen until it is closed (window X, Esc or Q)."""
    import pygame

    while not close_requested(pygame.event.get()):
        time.sleep(0.05)
    pygame.quit()


def run_episode(seed, replay, env_cls):
    """Play starter bot vs starter bot in env_cls through the official runner, as
    `luxai-s2 BOT BOT -s SEED -v 1 -o REPLAY` would; save the replay and return the final scores."""
    import numpy as np
    from luxai_runner.episode import Episode, EpisodeConfig, ReplayConfig

    # The runner starts Python bots with the `python` on PATH: make it this environment's, as activating it would.
    os.environ["PATH"] = os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")
    os.chdir(REPO)
    if seed:  # luxai_runner/cli.py does the same, skipping seed 0
        np.random.seed(seed)
    cfg = EpisodeConfig(
        players=[STARTER_BOT, STARTER_BOT],
        env_cls=env_cls,
        seed=seed,
        env_cfg=dict(verbose=1, validate_action_space=True, max_episode_length=1000),
        verbosity=1,
        save_replay_path=str(replay),
        replay_options=ReplayConfig(save_format="json", compressed_obs=True),
        render=False,
    )
    return asyncio.run(Episode(cfg=cfg).run())


def turn_label(turn):
    # Turns are negative while the teams bid and place their factories.
    return "setup" if turn < 0 else f"turn {turn}"


def live_title(seed, turn):
    return f"lux live — seed {seed} — {turn_label(turn)}"


def band_text(seed, turn, factories, scores):
    """The lines of lux live's band: the game, then each team.

    `scores` are the engine's step rewards: a team's lichen, or -1000 once it has lost every factory.
    """
    lines = [f"seed {seed} · {turn_label(turn)}"]
    for player in ("player_0", "player_1"):
        score, count = int(scores.get(player, 0)), factories[player]
        if score == -1000:
            lines.append(f"{player}: lost all factories")
        else:
            lines.append(f"{player}: {count} factor{'y' if count == 1 else 'ies'}, {score} lichen")
    return lines


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
    seed_help = f"map seed (default: random, 0-{MAX_RANDOM_SEED})"
    web = commands.add_parser("web", usage="lux web [SEED]\n       lux web --replay [N | PATH]",
                              help="play a match and watch it in the browser, or watch a saved one")
    web.add_argument("seed", nargs="?", type=int, metavar="SEED", help=seed_help)
    web.add_argument("--replay", nargs="?", const="1", metavar="N | PATH",
                     help="watch a saved replay instead of playing: N counts back from the newest in replays/ "
                          "(default 1, the newest), anything else is a file path")
    live_parser = commands.add_parser(
        "live", usage="lux live [SEED] [--delay S]", help="play a match in the engine's window, turn by turn",
        description="Above the board: the seed, the turn, and each team's factories and lichen. "
                    "Close the window with its X, Esc or Q. Closing it mid-game stops the game without saving "
                    "its replay; otherwise the replay is saved in replays/ like lux web's.")
    live_parser.add_argument("seed", nargs="?", type=int, metavar="SEED", help=seed_help)
    live_parser.add_argument("--delay", type=float, default=LIVE_DELAY, metavar="S",
                             help=f"pause after drawing each turn, in seconds (default {LIVE_DELAY}; 0 = no pause)")
    listing = commands.add_parser("list", usage="lux list [--all]", help="list the saved replays, newest last")
    listing.add_argument("--all", action="store_true", help=f"show every replay, not just the {LIST_LIMIT} newest")
    args = parser.parse_args(argv)

    try:
        if args.command == "list":
            print(format_list(REPLAYS_DIR, show_all=args.all))
            return 0
        if getattr(args, "replay", None) is not None:
            if args.seed is not None:
                web.error("give a seed to play, or --replay to watch a saved replay, not both")
            replay = resolve_replay(args.replay, REPLAYS_DIR)
        else:
            if args.command == "live" and args.delay < 0:
                live_parser.error("--delay can't be negative")
            seed = args.seed if args.seed is not None else random.randint(0, MAX_RANDOM_SEED)
            replay = REPLAYS_DIR / replay_name(datetime.now(), seed)
            print(f"Playing seed {seed}...")
            scores = live(seed, replay, args.delay) if args.command == "live" else play(seed, replay)
            _, turn = replay_summary(replay)
            points = ", ".join(f"{player} {int(score)}" for player, score in scores.items())
            print(f"Seed {seed}: game over at turn {turn}. Scores: {points} ({outcome(scores)}).")
            print(f"Replay: {replay}")
            if args.command == "live":
                print("Close the game window (X, Esc or Q) to finish.")
                wait_until_closed()
                return 0
        if not show(replay, open_browser=open_in_browser):
            raise LuxError(f"the browser did not load the replay within {VIEWER_TIMEOUT} s; "
                           f"try again with: lux web --replay {replay}")
    except LuxError as error:
        print(f"lux: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
