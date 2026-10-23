"""difftest: the Python engine and the C engine side by side.

    difftest.py [--seed S] [--games N] [--orders LIST] [--malformed P] [--map-size N]

Each game is set up by the Python engine (bid and factory placement, which C
doesn't have yet), copied into C, then played in both engines with the same random
orders. After every turn the whole game is compared: the board, every unit and
factory with its queue, the teams, the rewards and who failed. The run stops at
the first difference and prints it with the command that repeats it: a seed and
the options give the same games every time.

--orders picks the order types sent: "all" (default), "none" (both engines only
run the end-of-turn rules), or a comma list of move, transfer, pickup, dig,
self_destruct, recharge, build, water. Board-invalid orders (moving off the map,
digging without power...) are always in the mix: they are part of the rules.

--malformed P: on each turn, with probability P, one player gets one malformed
order (a key that isn't theirs, a number out of range, a queue over 20, a bad
factory action). Python fails that player and the game ends after the turn, so
this checks the failure paths; keep P small.

Run it with the repository's .venv:
    make -C c-engine && .venv/bin/python c-engine/tests/difftest.py --games 20
"""
import argparse
import contextlib
import io
import random
import sys
import time

import numpy as np

import loader
import luxc
from luxai_s2.env import LuxAI_S2
from luxai_s2.team import FactionTypes
from luxai_s2.utils.utils import my_turn_to_place_factory

PLAYERS = loader.PLAYERS
UNIT_ORDERS = {"move": 0, "transfer": 1, "pickup": 2, "dig": 3, "self_destruct": 4, "recharge": 5}
LUX_MOVE = UNIT_ORDERS["move"]
FACTORY_ORDERS = {"build": (0, 1), "water": (2,)}
FACTIONS = [f.name for f in FactionTypes]


def parse_orders(text):
    if text == "all":
        names = list(UNIT_ORDERS) + list(FACTORY_ORDERS)
    elif text == "none":
        names = []
    else:
        names = [name.strip() for name in text.split(",") if name.strip()]
    unknown = [n for n in names if n not in UNIT_ORDERS and n not in FACTORY_ORDERS]
    if unknown:
        raise SystemExit(f"unknown order types: {', '.join(unknown)}")
    unit_types = [UNIT_ORDERS[n] for n in names if n in UNIT_ORDERS]
    factory_codes = [code for n in names if n in FACTORY_ORDERS for code in FACTORY_ORDERS[n]]
    return unit_types, factory_codes


# ---- Random orders --------------------------------------------------------------------


class Orders:
    """Random orders from a seeded generator. Values are mostly small, so that
    orders often do something, with the occasional extreme in range."""

    def __init__(self, rng, unit_types, factory_codes, malformed):
        self.rng = rng
        self.unit_types = unit_types
        self.factory_codes = factory_codes
        self.malformed = malformed

    def _amount(self):
        rng = self.rng
        return rng.choice([0, 1, rng.randint(0, 20), rng.randint(0, 200), rng.randint(0, 3001)])

    def _action(self):
        rng = self.rng
        return [
            rng.choice(self.unit_types),
            rng.randint(0, 4),
            rng.randint(0, 4),
            self._amount(),
            rng.choice([0, 0, 0, 1, 2, rng.randint(0, 9999)]),
            rng.choice([1, 1, 1, 2, 3, rng.randint(1, 20), rng.randint(1, 9999)]),
        ]

    def _queue(self):
        length = self.rng.choice([0, 1, 1, 1, 2, 3, 5, 20])
        return np.array([self._action() for _ in range(length)], dtype=np.int64).reshape(-1, 6)

    def turn(self, state):
        rng = self.rng
        actions = {}
        for player in PLAYERS:
            mine = {}
            if self.unit_types:
                for unit_id in state.units[player]:
                    if rng.random() < 0.25:
                        mine[unit_id] = self._queue()
            if self.factory_codes:
                for factory_id in state.factories[player]:
                    if rng.random() < 0.3:
                        mine[factory_id] = rng.choice(self.factory_codes)
            actions[player] = mine
        if LUX_MOVE in self.unit_types:
            self._herd(state, actions)
            self._rally(state, actions)
        if self.malformed and rng.random() < self.malformed:
            self._break_one(state, actions)
        return actions

    def _herd(self, state, actions):
        """Keeps robots crowded: about half of those without other orders step toward
        their nearest factory's center, or off it if they are on it."""
        rng = self.rng
        for player in PLAYERS:
            factories = list(state.factories[player].values())
            if not factories:
                continue
            for u in state.units[player].values():
                if u.unit_id in actions[player] or rng.random() < 0.5:
                    continue
                ux, uy = int(u.pos.x), int(u.pos.y)
                f = min(factories, key=lambda f: abs(int(f.pos.x) - ux) + abs(int(f.pos.y) - uy))
                dx, dy = int(f.pos.x) - ux, int(f.pos.y) - uy
                if dx == 0 and dy == 0:
                    direction = rng.randint(1, 4)
                elif abs(dx) >= abs(dy):
                    direction = 2 if dx > 0 else 4
                else:
                    direction = 3 if dy > 0 else 1
                actions[player][u.unit_id] = np.array([[LUX_MOVE, direction, 0, 0, 0, 1]], dtype=np.int64)

    def _rally(self, state, actions):
        """Random orders almost never send two robots onto one tile in the same turn,
        so collisions decided by power would never come up. Pick a few tiles (where
        a robot stands, or a factory center) and send every robot next to one onto it."""
        rng = self.rng
        units = [u for player in PLAYERS for u in state.units[player].values()]
        if not units:
            return
        spots = [(int(u.pos.x), int(u.pos.y)) for u in rng.sample(units, min(len(units), rng.randint(0, 3)))]
        factories = [f for player in PLAYERS for f in state.factories[player].values()]
        if factories and rng.random() < 0.5:
            f = rng.choice(factories)
            spots.append((int(f.pos.x), int(f.pos.y)))
        for x, y in spots:
            for u in units:
                dx, dy = x - int(u.pos.x), y - int(u.pos.y)
                if abs(dx) + abs(dy) != 1 or rng.random() < 0.2:
                    continue
                direction = {(0, -1): 1, (1, 0): 2, (0, 1): 3, (-1, 0): 4}[(dx, dy)]
                actions[u.team.agent][u.unit_id] = np.array([[LUX_MOVE, direction, 0, 0, 0, 1]], dtype=np.int64)

    def _break_one(self, state, actions):
        rng = self.rng
        player = rng.choice(PLAYERS)
        other = PLAYERS[1 - PLAYERS.index(player)]
        mine = actions[player]
        own_units = list(state.units[player])
        own_factories = list(state.factories[player])
        kinds = ["foreign_key", "unknown_key", "other_key"]
        if own_units:
            kinds += ["out_of_range", "bad_type", "long_queue"]
        if own_factories:
            kinds += ["bad_factory_action"]
        kind = rng.choice(kinds)
        if kind == "foreign_key":
            theirs = list(state.units[other]) + list(state.factories[other])
            key = rng.choice(theirs) if theirs else "unit_999999"
            mine[key] = 0 if key.startswith("factory") else np.zeros((0, 6), dtype=np.int64)
        elif kind == "unknown_key":
            key = rng.choice(["unit_999999", "factory_999999", "unit_007"])
            mine[key] = 0 if key.startswith("factory") else np.zeros((0, 6), dtype=np.int64)
        elif kind == "other_key":
            mine["robot_1"] = np.zeros((0, 6), dtype=np.int64)
        elif kind == "out_of_range":
            row = self._action() if self.unit_types else [0, 0, 0, 0, 0, 1]
            field = rng.randint(0, 5)
            row[field] = rng.choice([-1, 6, 5000, 10000, 3002])
            mine[rng.choice(own_units)] = np.array([row], dtype=np.int64)
        elif kind == "bad_type":
            mine[rng.choice(own_units)] = np.array([[rng.choice([-1, 6, 7]), 0, 0, 0, 0, 1]], dtype=np.int64)
        elif kind == "long_queue":
            types = self.unit_types or [0]
            rows = [[rng.choice(types), rng.randint(0, 4), 0, 0, 0, 1] for _ in range(rng.randint(21, 25))]
            mine[rng.choice(own_units)] = np.array(rows, dtype=np.int64)
        elif kind == "bad_factory_action":
            mine[rng.choice(own_factories)] = rng.choice([-1, 3, 4])
        # Python reads the dict in order: put the broken entry at a random place in it
        items = list(mine.items())
        rng.shuffle(items)
        actions[player] = dict(items)


# ---- What the games exercised -----------------------------------------------------------


class Coverage:
    """Counts what happened in the Python engine during the run, by wrapping some of
    its functions (they still run unchanged). "No difference" only means something
    for the situations that actually came up."""

    def __init__(self):
        self.counts = {}

    def add(self, what, n=1):
        self.counts[what] = self.counts.get(what, 0) + n

    def install(self):
        import luxai_s2.env as lux_env

        cov = self
        validate_actions = lux_env.validate_actions
        top_two = lux_env.get_top_two_power_units
        env_cls = lux_env.LuxAI_S2
        add_unit = env_cls.add_unit
        destroy_unit = env_cls.destroy_unit
        destroy_factory = env_cls.destroy_factory
        movement = env_cls._handle_movement_actions
        transfer = env_cls._handle_transfer_actions
        pickup = env_cls._handle_pickup_actions
        in_movement = [False]

        def counted_validate_actions(env_cfg, state, actions_by_type, verbose=1):
            given = {kind: len(entries) for kind, entries in actions_by_type.items()}
            valid = validate_actions(env_cfg, state, actions_by_type, verbose=verbose)
            for kind, n in given.items():
                cov.add(f"{kind} orders carried out", len(valid[kind]))
                cov.add(f"{kind} orders dropped as invalid", n - len(valid[kind]))
            return valid

        def counted_top_two(units, unit_type):
            most, next_most = top_two(units, unit_type)
            powers = sorted((u.power for u in units if u.unit_type == unit_type), reverse=True)
            cov.add("collisions decided by power")
            if most.power == next_most.power:
                cov.add("  ... a tie: all destroyed")
            elif next_most.power != powers[1]:
                cov.add("  ... with the wrong second-strongest robot (quirk 1)")
            return most, next_most

        def counted_add_unit(self, team, unit_type, pos):
            cov.add("robots built")
            return add_unit(self, team, unit_type, pos)

        def counted_destroy_unit(self, unit):
            cov.add("robots destroyed in collisions" if in_movement[0] else "robots self-destructed")
            return destroy_unit(self, unit)

        def counted_destroy_factory(self, factory):
            cov.add("factories run dry")
            return destroy_factory(self, factory)

        def counted_movement(self, actions_by_type):
            units_map = self.state.board.units_map
            crowded = {k: list(v) for k, v in units_map.items() if len(v) > 1}
            cov.add("tiles where a factory built under a standing robot", len(crowded))
            entered = {
                f"{u.pos.x + lux_env.move_deltas[a.move_dir][0]},{u.pos.y + lux_env.move_deltas[a.move_dir][1]}"
                for u, a in actions_by_type["move"]
                if a.move_dir != 0
            }
            in_movement[0] = True
            try:
                result = movement(self, actions_by_type)
            finally:
                in_movement[0] = False
            for key, before in crowded.items():
                heavies = [u for u in before if u.unit_type.name == "HEAVY"]
                after = self.state.board.units_map.get(key, [])
                if len(heavies) >= 2 and after and after[0].unit_type.name == "LIGHT":
                    cov.add("  ... a light survived two standing heavies (quirk 2)")
                if key not in entered and not after:
                    cov.add("  ... nobody entered and all standing robots were destroyed (quirk 3)")
            return result

        def counted_transfer(self, actions_by_type):
            for unit, _ in actions_by_type["transfer"]:
                if unit.unit_id not in self.state.units[unit.team.agent]:
                    cov.add("transfers by robots destroyed that turn (quirk 4)")
            return transfer(self, actions_by_type)

        def counted_pickup(self, actions_by_type):
            for unit, _ in actions_by_type["pickup"]:
                if unit.unit_id not in self.state.units[unit.team.agent]:
                    cov.add("pickups by robots destroyed that turn (quirk 4)")
            return pickup(self, actions_by_type)

        lux_env.validate_actions = counted_validate_actions
        lux_env.get_top_two_power_units = counted_top_two
        env_cls.add_unit = counted_add_unit
        env_cls.destroy_unit = counted_destroy_unit
        env_cls.destroy_factory = counted_destroy_factory
        env_cls._handle_movement_actions = counted_movement
        env_cls._handle_transfer_actions = counted_transfer
        env_cls._handle_pickup_actions = counted_pickup

    # the report's order; zeros are shown, so what never happened is visible
    REPORT_ORDER = [
        *[
            f"{kind} orders {verdict}"
            for kind in ("move", "transfer", "pickup", "dig", "self_destruct", "recharge", "factory_build", "factory_water")
            for verdict in ("carried out", "dropped as invalid")
        ],
        "robots built",
        "robots self-destructed",
        "robots destroyed in collisions",
        "collisions decided by power",
        "  ... a tie: all destroyed",
        "  ... with the wrong second-strongest robot (quirk 1)",
        "tiles where a factory built under a standing robot",
        "  ... a light survived two standing heavies (quirk 2)",
        "  ... nobody entered and all standing robots were destroyed (quirk 3)",
        "transfers by robots destroyed that turn (quirk 4)",
        "pickups by robots destroyed that turn (quirk 4)",
        "factories run dry",
        "players who lost all their factories",
        "players failed by a malformed order",
    ]

    def report(self):
        keys = self.REPORT_ORDER + [k for k in self.counts if k not in self.REPORT_ORDER]
        width = max(len(k) for k in keys)
        return [f"  {k.ljust(width)}  {self.counts.get(k, 0)}" for k in keys]


# ---- Setting a game up in Python --------------------------------------------------------


def play_setup(env, rng):
    """The bid and the factory placement, by the Python engine, with random but legal
    choices. Each team spreads its water and metal one of three ways: 150 each like
    the starter bot, at random, or all of it into its first factory (which can then
    last the whole 1000 turns, while the others run dry at once). Returns False if a
    player failed (so the game never started)."""
    bids = {p: {"faction": rng.choice(FACTIONS), "bid": rng.choice([0, 0, 0, rng.randint(-10, 10)])} for p in PLAYERS}
    spread = {p: rng.choice(["starter", "starter", "random", "hoard"]) for p in PLAYERS}
    _, _, terminations, _, _ = quiet_step(env, bids)
    if any(terminations.values()):
        return False
    while env.state.real_env_steps < 0:
        actions = {}
        for player in PLAYERS:
            team = env.state.teams[player]
            if team.factories_to_place > 0 and my_turn_to_place_factory(team.place_first, env.state.env_steps):
                spawns = np.argwhere(env.state.board.valid_spawns_mask)
                x, y = spawns[rng.randrange(len(spawns))]
                if spread[player] == "starter":
                    water, metal = 150, 150
                elif spread[player] == "random":
                    water = rng.randint(0, int(team.init_water))
                    metal = rng.randint(0, int(team.init_metal))
                else:
                    water, metal = int(team.init_water), int(team.init_metal)
                actions[player] = {"spawn": [int(x), int(y)], "water": water, "metal": metal}
            else:
                actions[player] = {}
        _, _, terminations, _, _ = quiet_step(env, actions)
        if any(terminations.values()):
            return False
    return True


def quiet_step(env, actions):
    """env.step without the tracebacks Python prints for a player's bad orders."""
    with contextlib.redirect_stdout(io.StringIO()):
        return env.step(actions)


# ---- One game -------------------------------------------------------------------------


def play_game(lib, ws, seed, args, unit_types, factory_codes, coverage):
    """Plays one game in both engines. Returns (turns played, None) if they agree
    every turn, or (turn, report lines) at the first difference."""
    rng = random.Random(seed)
    env_kwargs = {"verbose": 0}
    if args.map_size:
        env_kwargs["map_size"] = args.map_size
    env = LuxAI_S2(**env_kwargs)
    env.reset(seed=seed)
    if not play_setup(env, rng):
        return 0, None

    problems = loader.check_python_invariants(env)
    if problems:
        return 0, ["Python broke an assumption of the C engine after setup:"] + problems

    cstate = loader.load_state(lib, env)
    try:
        diffs = loader.compare(loader.snapshot_python(env), loader.snapshot_c(cstate))
        if diffs:
            return 0, ["the loaded C state differs from Python before any turn:"] + diffs

        orders = Orders(rng, unit_types, factory_codes, args.malformed)
        result = luxc.StepResult()
        turn = 0
        while env.agents:
            turn = env.state.real_env_steps
            actions = orders.turn(env.state)
            commands, keep = loader.commands_from_actions(actions)
            _, rewards, terminations, truncations, _ = quiet_step(env, actions)
            for player in PLAYERS:
                if truncations[player]:
                    lost = not env.state.factories[player]
                    coverage.add("players who lost all their factories" if lost else "players failed by a malformed order")
            rc = lib.lux_step(cstate, ws, commands, result)
            del keep
            if rc != luxc.OK:
                return turn, [f"lux_step returned {luxc.error_name(rc)}; Python played the turn"]

            report = []
            for t, player in enumerate(PLAYERS):
                if int(rewards[player]) != result.rewards[t]:
                    report.append(f"reward of {player}: python {int(rewards[player])}, C {result.rewards[t]}")
                if bool(truncations[player]) != bool(result.failed[t]):
                    report.append(f"failed {player}: python {bool(truncations[player])}, C {bool(result.failed[t])}")
                if bool(terminations[player]) != bool(result.done):
                    report.append(f"done for {player}: python {bool(terminations[player])}, C {bool(result.done)}")
            report += loader.compare(loader.snapshot_python(env), loader.snapshot_c(cstate))
            if report:
                return turn, ["after this turn's orders:", *describe_actions(actions)] + ["differences:"] + report
            if env.agents:
                problems = loader.check_python_invariants(env)
                if problems:
                    return turn, ["Python broke an assumption of the C engine:"] + problems
        return turn + 1, None
    finally:
        lib.lux_state_free(cstate)


def describe_actions(actions, limit=40):
    lines = []
    for player in PLAYERS:
        for key, value in actions.get(player, {}).items():
            if isinstance(value, np.ndarray):
                value = value.tolist()
            lines.append(f"  {player} {key}: {value}")
    if len(lines) > limit:
        lines = lines[:limit] + [f"  ... and {len(lines) - limit} more"]
    return lines or ["  (none)"]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compare the C engine with the Python engine, turn by turn.")
    parser.add_argument("--seed", type=int, default=0, help="first game's seed (default 0)")
    parser.add_argument("--games", type=int, default=10, help="number of games, seeds seed..seed+games-1 (default 10)")
    parser.add_argument("--orders", default="all", help='"all", "none", or a comma list (see the module doc)')
    parser.add_argument("--malformed", type=float, default=0.0, help="per-turn chance of a malformed order (default 0)")
    parser.add_argument("--map-size", type=int, default=None, help="map size (default: the engine's, 64)")
    parser.add_argument("--quiet", action="store_true", help="no line per game, only the summary")
    args = parser.parse_args(argv)
    unit_types, factory_codes = parse_orders(args.orders)

    lib = luxc.load()
    coverage = Coverage()
    coverage.install()
    ws = lib.lux_workspace_new()
    if not ws:
        raise MemoryError("lux_workspace_new failed")
    total_turns = 0
    lengths = []
    start = time.monotonic()
    try:
        for seed in range(args.seed, args.seed + args.games):
            turns, report = play_game(lib, ws, seed, args, unit_types, factory_codes, coverage)
            total_turns += turns
            lengths.append(turns)
            if report:
                print(f"seed {seed}: DIFFERENT at turn {turns}")
                for line in report:
                    print(f"  {line}")
                repeat = [f"--seed {seed}", "--games 1", f"--orders {args.orders}"]
                if args.malformed:
                    repeat.append(f"--malformed {args.malformed}")
                if args.map_size:
                    repeat.append(f"--map-size {args.map_size}")
                print(f"repeat it: .venv/bin/python c-engine/tests/difftest.py {' '.join(repeat)}")
                return 1
            if not args.quiet:
                print(f"seed {seed}: same for {turns} turns")
    finally:
        lib.lux_workspace_free(ws)
    print(f"{args.games} games, {total_turns} turns, no difference ({time.monotonic() - start:.1f} s)")
    full = sum(1 for n in lengths if n >= 1000)
    print(f"game lengths: shortest {min(lengths)}, longest {max(lengths)}; {full} played all 1000 turns")
    print("what the games exercised (counted in the Python engine):")
    for line in coverage.report():
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
