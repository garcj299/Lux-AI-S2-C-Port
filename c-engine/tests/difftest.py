"""difftest: the Python engine and the C engine side by side.

    difftest.py [--seed S] [--games N] [--orders LIST] [--malformed P] [--map-size N]
                [--require KEYS] [--lib PATH] [--quiet]

Each game is set up by the Python engine (bid and factory placement, which C
doesn't have yet), copied into C, then played in both engines with the same
orders. After every turn the whole game is compared: the board, every unit and
factory with its queue, the teams, the rewards and who failed. The run stops at
the first difference and prints it with the command that repeats it: a seed and
the options give the same games every time.

Most robots and factories get random orders; some robots prospect (dig ice or
ore and bring it home) and some factories garden (water every turn), since random
orders never reach those rules (see Orders).

--orders picks the order types sent: "all" (default), "none" (both engines only
run the end-of-turn rules), or a comma list of move, transfer, pickup, dig,
self_destruct, recharge, build, water. Board-invalid orders (moving off the map,
digging without power...) are always in the mix: they are part of the rules.

--malformed P: on each turn, with probability P, one player gets one malformed
order (a key that isn't theirs, a number out of range, a queue over 20, a bad
factory action). Python fails that player and the game ends after the turn, so
this checks the failure paths; keep P small.

--require KEYS: tally keys (harness.Coverage) that must not stay at 0; the run
fails if one does, as its rule went untested. --lib PATH tests another build.

Run it with the repository's .venv:
    make -C c-engine && .venv/bin/python c-engine/tests/difftest.py --games 20
"""
import argparse
import random
import sys
import time

import numpy as np

import harness
import loader
import luxc

PLAYERS = harness.PLAYERS
UNIT_ORDERS = {"move": 0, "transfer": 1, "pickup": 2, "dig": 3, "self_destruct": 4, "recharge": 5}
LUX_MOVE, LUX_TRANSFER, LUX_PICKUP, LUX_DIG = 0, 1, 2, 3
FACTORY_ORDERS = {"build": (0, 1), "water": (2,)}
LUX_WATER = 2
# direction -> (dx, dy), as actions.py's move_deltas
DELTAS = {1: (0, -1), 2: (1, 0), 3: (0, 1), 4: (-1, 0)}


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
    orders often do something, with the occasional extreme in range.

    Random orders alone never touch the resource economy or grow lichen far (a
    robot that digs ice must first walk to it, and lichen spreads only after about
    20 turns of watering in a row). So some robots are prospectors and some
    factories gardeners, with a purpose instead of random orders; the others get
    random ones. Each robot and factory gets its role when first seen."""

    def __init__(self, rng, unit_types, factory_codes, malformed):
        self.rng = rng
        self.unit_types = unit_types
        self.factory_codes = factory_codes
        self.malformed = malformed
        # prospectors need every order they use; gardeners need water orders
        self.prospecting = {LUX_MOVE, LUX_TRANSFER, LUX_PICKUP, LUX_DIG} <= set(unit_types)
        self.gardening = LUX_WATER in factory_codes
        self.roles = {}
        self.resources = None  # the ice and ore tiles, which never change after setup

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
            for unit_id, unit in state.units[player].items():
                if self._role(unit_id, "prospector", 0.3 if self.prospecting else 0) == "prospector":
                    queue = self._prospect(state, player, unit)
                    if queue is not None:
                        mine[unit_id] = queue
                elif self.unit_types and rng.random() < 0.25:
                    mine[unit_id] = self._queue()
            for factory_id, factory in state.factories[player].items():
                rich = factory.cargo.water >= 300
                chance = (0.75 if rich else 0.2) if self.gardening else 0
                if self._role(factory_id, "gardener", chance) == "gardener":
                    if factory.cargo.water - factory.water_cost(state.env_cfg) >= 30:
                        mine[factory_id] = LUX_WATER
                elif self.factory_codes and rng.random() < 0.3:
                    mine[factory_id] = rng.choice(self.factory_codes)
            actions[player] = mine
        if LUX_MOVE in self.unit_types:
            self._herd(state, actions)
            self._rally(state, actions)
        if self.malformed and rng.random() < self.malformed:
            self._break_one(state, actions)
        return actions

    def _role(self, key, role, chance):
        """key's role: `role` with this chance when first seen, else "random"."""
        if key not in self.roles:
            self.roles[key] = role if chance and self.rng.random() < chance else "random"
        return self.roles[key]

    def _random(self, unit):
        return self.roles.get(unit.unit_id) == "random"

    # ---- Prospectors ----

    def _prospect(self, state, player, unit):
        """A prospector's new queue, or None to leave its queue alone. It walks to
        the nearest tile of its resource, digs until it holds its load or runs low on
        power, walks home, hands the load to the factory and picks up power. Now and
        then it replans with a queue still full, which also frees one stuck behind a
        dropped order (an enemy factory in the way, too little power to move)."""
        rng = self.rng
        if unit.action_queue and rng.random() < 0.85:
            return None
        plan = self.roles.setdefault(
            f"{unit.unit_id} plan",
            {"resource": rng.choice(["ice", "ore"]), "load": rng.choice([0.1, 0.5, 1.0, 2.0])},
        )
        ucfg = unit.unit_cfg
        x, y = int(unit.pos.x), int(unit.pos.y)
        factories = list(state.factories[player].values())
        if not factories:
            return None
        home = min(factories, key=lambda f: max(abs(int(f.pos.x) - x), abs(int(f.pos.y) - y)))
        cargo = int(unit.cargo.ice) + int(unit.cargo.ore)
        if cargo >= plan["load"] * ucfg.CARGO_SPACE or unit.power < ucfg.BATTERY_CAPACITY // 5:
            side = self._side_of(home, x, y)
            if side is None:
                return self._walk(state, player, x, y, int(home.pos.x), int(home.pos.y))
            # hand over the cargo (direction 0 is the factory tile it stands on),
            # then fill the battery
            rows = [[LUX_TRANSFER, side, r, int(amount), 0, 1] for r, amount in ((0, unit.cargo.ice), (1, unit.cargo.ore)) if amount]
            rows.append([LUX_PICKUP, 0, 4, max(ucfg.BATTERY_CAPACITY - int(unit.power), 0), 0, 1])
            if side != 0:
                rows = rows[:-1] + [[LUX_MOVE, side, 0, 0, 0, 1]]
            return np.array(rows, dtype=np.int64)
        tx, ty = self._nearest_resource(state, plan["resource"], x, y)
        if (tx, ty) == (x, y):
            return np.array([[LUX_DIG, 0, 0, 0, rng.choice([0, 0, 1]), rng.randint(1, 8)]], dtype=np.int64)
        return self._walk(state, player, x, y, tx, ty)

    @staticmethod
    def _side_of(factory, x, y):
        """0 if (x, y) is one of the factory's tiles, the direction of the factory if
        (x, y) is next to one of them, else None."""
        fx, fy = int(factory.pos.x), int(factory.pos.y)
        if abs(fx - x) <= 1 and abs(fy - y) <= 1:
            return 0
        for direction, (dx, dy) in DELTAS.items():
            if abs(fx - x - dx) <= 1 and abs(fy - y - dy) <= 1:
                return direction
        return None

    def _nearest_resource(self, state, resource, x, y):
        if self.resources is None:
            board = state.board
            self.resources = {
                "ice": [tuple(map(int, p)) for p in np.argwhere(board.ice > 0)],
                "ore": [tuple(map(int, p)) for p in np.argwhere(board.ore > 0)],
            }
        tiles = self.resources[resource] or self.resources["ice"] + self.resources["ore"]
        return min(tiles, key=lambda p: abs(p[0] - x) + abs(p[1] - y))

    def _walk(self, state, player, x, y, tx, ty):
        """A queue moving from (x, y) toward (tx, ty) along one axis, around enemy
        factories when it can."""
        rng = self.rng
        dx, dy = tx - x, ty - y
        steps = []
        if dx:
            steps.append((2 if dx > 0 else 4, abs(dx)))
        if dy:
            steps.append((3 if dy > 0 else 1, abs(dy)))
        steps.sort(key=lambda step: -step[1])
        occupancy = state.board.factory_occupancy_map
        own = {int(f.num_id) for f in state.factories[player].values()}
        size = state.env_cfg.map_size
        for direction, distance in steps:
            nx, ny = x + DELTAS[direction][0], y + DELTAS[direction][1]
            if 0 <= nx < size and 0 <= ny < size and occupancy[nx, ny] in (-1, *own):
                n = min(distance, rng.randint(1, 6))
                return np.array([[LUX_MOVE, direction, 0, 0, 0, n]], dtype=np.int64)
        return np.array([[LUX_MOVE, rng.randint(1, 4), 0, 0, 0, 1]], dtype=np.int64)

    # ---- Crowding, for collisions ----

    def _herd(self, state, actions):
        """Keeps robots crowded: about half of the random ones without other orders
        step toward their nearest factory's center, or off it if they are on it."""
        rng = self.rng
        for player in PLAYERS:
            factories = list(state.factories[player].values())
            if not factories:
                continue
            for u in state.units[player].values():
                if u.unit_id in actions[player] or not self._random(u) or rng.random() < 0.5:
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
        a robot stands, or a factory center) and send every random robot next to one
        onto it."""
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
                if abs(dx) + abs(dy) != 1 or not self._random(u) or rng.random() < 0.2:
                    continue
                direction = {(0, -1): 1, (1, 0): 2, (0, 1): 3, (-1, 0): 4}[(dx, dy)]
                actions[u.team.agent][u.unit_id] = np.array([[LUX_MOVE, direction, 0, 0, 0, 1]], dtype=np.int64)

    # ---- Malformed orders ----

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


# ---- One game -------------------------------------------------------------------------


def play_game(lib, ws, seed, args, unit_types, factory_codes, coverage):
    """Plays one game in both engines. Returns (turns played, None) if they agree
    every turn, or (turn, report lines) at the first difference."""
    rng = random.Random(seed)
    env = harness.new_game(seed, rng, args.map_size)
    if env is None:
        # play_setup makes only legal choices, so this is the tester's bug
        return 0, ["Python refused a bid or a factory placement during setup"]

    problems = loader.check_python_invariants(env)
    if problems:
        return 0, ["Python broke an assumption of the C engine after setup:"] + problems

    cstate = loader.load_state(lib, env)
    try:
        diffs = loader.compare(loader.snapshot_python(env), loader.snapshot_c(cstate))
        if diffs:
            return 0, ["the loaded C state differs from Python before any turn:"] + diffs

        orders = Orders(rng, unit_types, factory_codes, args.malformed)
        turn = 0
        while env.agents:
            turn = env.state.real_env_steps
            actions = orders.turn(env.state)
            report = harness.step_both(lib, ws, env, cstate, actions, coverage)
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
    parser.add_argument("--require", default="", help="tally keys that must not stay at 0, split by commas or spaces")
    parser.add_argument("--lib", default=luxc.LIB_PATH, help="the library to test (default: build/liblux.so)")
    parser.add_argument("--quiet", action="store_true", help="no line per game, only the summary")
    args = parser.parse_args(argv)
    unit_types, factory_codes = parse_orders(args.orders)
    required = args.require.replace(",", " ").split()
    unknown = [key for key in required if key not in harness.Coverage.LABELS and not key.startswith("raised_")]
    if unknown:
        raise SystemExit(f"unknown tally keys: {', '.join(unknown)}")

    lib = luxc.load(args.lib)
    coverage = harness.Coverage()
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
                print(f"seed {seed}: FAILED at turn {turns}")
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
    missing = coverage.missing(required)
    if missing:
        print("never happened, though required: " + ", ".join(f"{k} ({coverage.label(k).strip()})" for k in missing))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
