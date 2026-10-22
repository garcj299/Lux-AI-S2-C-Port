"""Tests for scripts/lux.py.

    .venv/bin/python -m unittest discover -s scripts

The end-to-end tests play real games (a few seconds each) and never open a real browser:
they point Python's `webbrowser` at a fake one through the BROWSER environment variable.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import lux


def write_replay(folder, name, seed, final_turn):
    """A replay file shaped like the runner's: compressed observations, the last one holding the turn."""
    path = Path(folder) / name
    data = {
        "observations": [{"real_env_steps": -7, "board": {}}, {"real_env_steps": final_turn, "board": {}}],
        "actions": [{"player_0": {}, "player_1": {}}],
        "metadata": {"seed": seed, "players": {"player_0": "a/main.py", "player_1": "b/main.py"}},
        "default_seed": seed,
    }
    path.write_text(json.dumps(data))
    return path


class ReplayNameTest(unittest.TestCase):
    def test_name_is_date_time_then_seed(self):
        self.assertEqual(lux.replay_name(datetime(2026, 10, 22, 8, 3, 5), 42), "2026-10-22_08-03-05_seed42.json")


class ResolveReplayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        # Written out of order on purpose: the order must come from the names.
        self.middle = write_replay(self.dir, "2026-10-22_10-00-00_seed2.json", 2, 151)
        self.newest = write_replay(self.dir, "2026-10-22_11-00-00_seed3.json", 3, 151)
        self.oldest = write_replay(self.dir, "2026-10-21_23-59-59_seed1.json", 1, 151)
        (self.dir / "notes.txt").write_text("not a replay")

    def tearDown(self):
        self.tmp.cleanup()

    def test_number_one_is_the_newest(self):
        self.assertEqual(lux.resolve_replay("1", self.dir), self.newest)

    def test_numbers_count_back_from_the_newest(self):
        self.assertEqual(lux.resolve_replay("2", self.dir), self.middle)
        self.assertEqual(lux.resolve_replay("3", self.dir), self.oldest)

    def test_number_past_the_oldest_is_an_error(self):
        with self.assertRaisesRegex(lux.LuxError, "3 replays"):
            lux.resolve_replay("4", self.dir)

    def test_zero_is_an_error(self):
        with self.assertRaises(lux.LuxError):
            lux.resolve_replay("0", self.dir)

    def test_anything_else_is_a_path(self):
        self.assertEqual(lux.resolve_replay(str(self.oldest), self.dir), self.oldest)

    def test_missing_path_is_an_error(self):
        with self.assertRaisesRegex(lux.LuxError, "no-such.json"):
            lux.resolve_replay(str(self.dir / "no-such.json"), self.dir)

    def test_empty_folder_is_an_error(self):
        with tempfile.TemporaryDirectory() as empty, self.assertRaises(lux.LuxError):
            lux.resolve_replay("1", Path(empty))


class ListTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        # 12 replays, seed N played at 10:N; seed 12 is the newest.
        for n in range(1, 13):
            write_replay(self.dir, f"2026-10-22_10-{n:02d}-00_seed{n}.json", n, 100 + n)

    def tearDown(self):
        self.tmp.cleanup()

    def test_shows_the_ten_newest_numbered_back_with_the_newest_last(self):
        lines = lux.format_list(self.dir).splitlines()
        entries = [line.split() for line in lines if line.split()[0].isdigit()]
        self.assertEqual([e[0] for e in entries], ["10", "9", "8", "7", "6", "5", "4", "3", "2", "1"])
        self.assertEqual(entries[-1], ["1", "2026-10-22", "10:12", "seed", "12", "112", "turns"])
        self.assertEqual(entries[0], ["10", "2026-10-22", "10:03", "seed", "3", "103", "turns"])

    def test_says_how_many_older_ones_are_hidden(self):
        first = lux.format_list(self.dir).splitlines()[0]
        self.assertIn("2 older", first)
        self.assertIn("--all", first)

    def test_all_shows_every_replay_and_hides_nothing(self):
        lines = lux.format_list(self.dir, show_all=True).splitlines()
        self.assertEqual(len(lines), 12)
        self.assertEqual(lines[0].split()[0], "12")
        self.assertNotIn("older", "\n".join(lines))

    def test_ten_or_fewer_hides_nothing(self):
        for n in (1, 2):
            (self.dir / f"2026-10-22_10-{n:02d}-00_seed{n}.json").unlink()
        self.assertNotIn("older", lux.format_list(self.dir))

    def test_empty_folder_says_how_to_make_one(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertIn("lux web", lux.format_list(Path(empty)))


class OutcomeTest(unittest.TestCase):
    def test_higher_score_wins(self):
        self.assertEqual(lux.outcome({"player_0": 12, "player_1": 3}), "player_0 wins")
        self.assertEqual(lux.outcome({"player_0": -1000, "player_1": 40}), "player_1 wins")

    def test_equal_scores_are_a_draw(self):
        self.assertEqual(lux.outcome({"player_0": -1000, "player_1": -1000}), "draw")


def fetch(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read()


class FakeBrowser:
    """Loads a viewer URL the way lux-eye-s2 does: the page, then the replay named in `#/?input=`."""

    def __init__(self):
        self.url = self.page = self.replay = None

    def __call__(self, url):
        self.url = url
        self.page = fetch(url)
        self.replay = fetch(self.replay_url())
        return True

    def replay_url(self):
        return parse_qs(urlsplit(urlsplit(self.url).fragment).query)["input"][0]


class ShowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.replay = write_replay(self.tmp.name, "2026-10-22_10-00-00_seed7.json", 7, 151)

    def tearDown(self):
        self.tmp.cleanup()

    def test_browser_gets_the_viewer_and_the_replay(self):
        browser = FakeBrowser()
        self.assertTrue(lux.show(self.replay, open_browser=browser, timeout=5))
        self.assertEqual(browser.page, (lux.VIEWER_DIR / "index.html").read_bytes())
        self.assertEqual(browser.replay, self.replay.read_bytes())

    def test_replay_is_served_from_the_viewer_origin(self):
        # Otherwise lux-eye-s2 fetches it through its online CORS proxy.
        browser = FakeBrowser()
        lux.show(self.replay, open_browser=browser, timeout=5)
        self.assertEqual(urlsplit(browser.replay_url()).netloc, urlsplit(browser.url).netloc)

    def test_server_stops_once_the_replay_is_loaded(self):
        browser = FakeBrowser()
        lux.show(self.replay, open_browser=browser, timeout=5)
        with self.assertRaises(urllib.error.URLError):
            fetch(browser.url)

    def test_gives_up_when_the_browser_never_asks(self):
        urls = []
        started = time.monotonic()
        self.assertFalse(lux.show(self.replay, open_browser=urls.append, timeout=0.5))
        self.assertLess(time.monotonic() - started, 3)
        with self.assertRaises(urllib.error.URLError):
            fetch(urls[0])

    def test_missing_viewer_is_an_error(self):
        with self.assertRaisesRegex(lux.LuxError, "viewer"):
            lux.show(self.replay, viewer_dir=Path(self.tmp.name) / "dist", open_browser=FakeBrowser(), timeout=5)


REAL_REPO = Path(__file__).resolve().parent.parent

FAKE_BROWSER = """#!{python}
# Stands in for the default browser: loads the page, then the replay, like lux-eye-s2.
import sys, urllib.request
from urllib.parse import parse_qs, urlsplit
url = sys.argv[1]
urllib.request.urlopen(url, timeout=5).read()
replay_url = parse_qs(urlsplit(urlsplit(url).fragment).query)["input"][0]
with open(sys.argv[0] + ".got", "wb") as got:
    got.write(urllib.request.urlopen(replay_url, timeout=5).read())
"""


class TempRepoTestCase(unittest.TestCase):
    """A copy of the scripts in a temporary repository whose .venv and third_party link to the
    real ones, so replays land in the temporary replays/ folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo = root / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        for name in ("lux", "lux.py", "install.sh"):
            source = REAL_REPO / "scripts" / name
            if source.exists():
                shutil.copy2(source, self.repo / "scripts" / name)
        (self.repo / ".venv").symlink_to(REAL_REPO / ".venv")
        (self.repo / "third_party").symlink_to(REAL_REPO / "third_party")
        self.replays = self.repo / "replays"
        self.bin = root / "bin"
        self.bin.mkdir()
        (self.bin / "lux").symlink_to(self.repo / "scripts" / "lux")
        self.elsewhere = root / "elsewhere"
        self.elsewhere.mkdir()
        self.browser = root / "browser"
        self.browser.write_text(FAKE_BROWSER.format(python=sys.executable))
        self.browser.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()


class CommandTest(TempRepoTestCase):
    """`lux` as the user runs it: through the ~/.local/bin-style link, from another folder."""

    def lux(self, *args):
        env = dict(os.environ, BROWSER=str(self.browser))
        return subprocess.run([str(self.bin / "lux"), *args], cwd=self.elsewhere, env=env,
                              capture_output=True, text=True, timeout=120)

    def browser_got(self):
        got = Path(str(self.browser) + ".got")
        return got.read_bytes() if got.exists() else None

    def add_replays(self):
        self.replays.mkdir()
        older = write_replay(self.replays, "2026-10-22_10-00-00_seed1.json", 1, 151)
        newer = write_replay(self.replays, "2026-10-22_11-00-00_seed2.json", 2, 149)
        return older, newer

    def test_web_plays_the_seed_saves_the_replay_and_shows_it(self):
        result = self.lux("web", "7")
        self.assertEqual(result.returncode, 0, result.stderr)
        replays = list(self.replays.glob("*.json"))
        self.assertEqual(len(replays), 1)
        self.assertRegex(replays[0].name, r"^\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d_seed7\.json$")
        self.assertEqual(json.loads(replays[0].read_text())["metadata"]["seed"], 7)
        # Bots that fail to start (wrong Python) still produce a replay, ended before turn 1.
        self.assertGreater(lux.replay_summary(replays[0])[1], 0, result.stderr)
        self.assertEqual(self.browser_got(), replays[0].read_bytes())
        self.assertIn(str(replays[0]), result.stdout)
        self.assertRegex(result.stdout, r"(player_[01] wins|draw)")

    def test_web_hides_pygame_build_noise(self):
        result = self.lux("web", "7")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("avx2", result.stderr)

    def test_web_without_a_seed_picks_one_and_names_the_replay_after_it(self):
        result = self.lux("web")
        self.assertEqual(result.returncode, 0, result.stderr)
        (replay,) = self.replays.glob("*.json")
        seed = json.loads(replay.read_text())["metadata"]["seed"]
        self.assertTrue(0 <= seed <= 999_999)
        self.assertTrue(replay.name.endswith(f"_seed{seed}.json"))
        self.assertIn(f"seed {seed}", result.stdout.lower())

    def test_replay_number_shows_a_saved_one_without_playing(self):
        older, newer = self.add_replays()
        result = self.lux("web", "--replay", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.browser_got(), older.read_bytes())
        self.assertEqual(len(list(self.replays.glob("*.json"))), 2)

    def test_replay_alone_shows_the_newest(self):
        older, newer = self.add_replays()
        result = self.lux("web", "--replay")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.browser_got(), newer.read_bytes())

    def test_replay_path_is_relative_to_the_current_folder(self):
        outside = write_replay(self.elsewhere, "kaggle.json", 99, 1000)
        result = self.lux("web", "--replay", "kaggle.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.browser_got(), outside.read_bytes())
        self.assertFalse(self.replays.exists())

    def test_seed_and_replay_together_are_refused_without_playing(self):
        self.add_replays()
        result = self.lux("web", "5", "--replay", "1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--replay", result.stderr)
        self.assertIsNone(self.browser_got())
        self.assertEqual(len(list(self.replays.glob("*.json"))), 2)

    def test_unknown_replay_number_is_a_short_error(self):
        self.add_replays()
        result = self.lux("web", "--replay", "9")
        self.assertEqual(result.returncode, 1)
        self.assertIn("lux list", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_list_reads_the_repository_replays_from_any_folder(self):
        older, newer = self.add_replays()
        result = self.lux("list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line.split()[0] for line in result.stdout.splitlines()], ["2", "1"])
        self.assertIn("seed 2", result.stdout.splitlines()[-1])

    def test_missing_environment_says_how_to_create_it(self):
        (self.repo / ".venv").unlink()
        result = self.lux("list")
        self.assertEqual(result.returncode, 1)
        self.assertIn("requirements.lock", result.stderr)


class InstallTest(TempRepoTestCase):
    def setUp(self):
        super().setUp()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.link = self.home / ".local" / "bin" / "lux"

    def install(self):
        return subprocess.run([str(self.repo / "scripts" / "install.sh")], env=dict(os.environ, HOME=str(self.home)),
                              capture_output=True, text=True, timeout=30)

    def test_links_lux_into_local_bin(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.link.resolve(), (self.repo / "scripts" / "lux").resolve())

    def test_running_it_again_is_fine(self):
        self.install()
        self.assertEqual(self.install().returncode, 0)

    def test_leaves_a_different_lux_alone(self):
        self.link.parent.mkdir(parents=True)
        self.link.write_text("someone else's lux")
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(self.link.read_text(), "someone else's lux")


if __name__ == "__main__":
    unittest.main()
