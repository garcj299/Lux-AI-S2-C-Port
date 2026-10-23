"""scenarios: hand-built turns for the cases random games rarely or never reach.

    scenarios.py [--seed S] [--lib PATH]

Each scenario takes a game just past setup, puts robots on chosen tiles with
chosen power (through the Python engine's own add_unit, as a factory would), gives
orders for one turn, plays that turn in both engines and compares everything, as
difftest does. Each one also checks that the case it is named after really
happened in Python, so a scenario can't quietly stop testing it.

The robots stand around a factory of player_0: its center and the tiles next to
it are the factory's own, so they hold no rubble and robots may stand on them.
"""
import argparse
import random
import sys

import numpy as np

import harness
import loader
import luxc
from luxai_s2.unit import UnitType

# directions: 1 up, 2 right, 3 down, 4 left
UP, RIGHT, DOWN, LEFT = 1, 2, 3, 4


def move(direction):
    return np.array([[0, direction, 0, 0, 0, 1]], dtype=np.int64)


class Scene:
    """A game past setup, and the helpers to place robots around one factory."""

    def __init__(self, env):
        self.env = env
        state = env.state
        size = state.env_cfg.map_size
        # a factory with three tiles to its right still on the map
        for f in state.factories["player_0"].values():
            if int(f.pos.x) + 3 < size:
                self.factory = f
                break
        else:
            raise RuntimeError("no factory of player_0 has room to its right")
        self.x, self.y = int(self.factory.pos.x), int(self.factory.pos.y)
        self.enemy = next(iter(state.factories["player_1"].values()))

    def clear_tile(self, x, y):
        """No rubble, ice, ore or lichen on (x, y)."""
        board = self.env.state.board
        board.rubble[x, y] = 0
        board.ice[x, y] = 0
        board.ore[x, y] = 0
        board.lichen[x, y] = 0
        board.lichen_strains[x, y] = -1

    def place(self, unit_type, x, y, power, player="player_0", ice=0):
        """A robot on (x, y) with this power; returns its id."""
        state = self.env.state
        if f"{x},{y}" in state.board.units_map:
            raise RuntimeError(f"({x}, {y}) is taken")
        unit = self.env.add_unit(state.teams[player], unit_type, np.array([x, y]))
        unit.power = power
        unit.cargo.ice = ice
        return unit.unit_id

    def alive(self, unit_id, player="player_0"):
        return unit_id in self.env.state.units[player]

    def power(self, unit_id, player="player_0"):
        return int(self.env.state.units[player][unit_id].power)


# Each scenario builds its robots, returns the turn's actions and a check. The
# check runs after the turn on the Python game and the tally's change, and says
# what went wrong if the case didn't happen.


def heavies_wrong_second(scene):
    """Three heavies enter the factory center in id order with power 100, 200, 50.
    get_top_two_power_units returns the strongest and the last one, not the real
    second, so the winner loses half of 20 (what the 50 has left), not of 70."""
    x, y = scene.x, scene.y
    a = scene.place(UnitType.HEAVY, x, y - 1, 100)
    b = scene.place(UnitType.HEAVY, x + 1, y, 200)
    c = scene.place(UnitType.HEAVY, x, y + 1, 50)
    actions = {"player_0": {a: move(DOWN), b: move(LEFT), c: move(UP)}, "player_1": {}}

    def check(tally):
        if not tally.get("quirk1"):
            return "the quirk didn't happen"
        if not scene.alive(b) or scene.alive(a) or scene.alive(c):
            return "the strongest heavy should be the only survivor"
        return None

    return actions, check


def lights_wrong_second(scene):
    """The same with lights: power 30, 60, 10."""
    x, y = scene.x, scene.y
    a = scene.place(UnitType.LIGHT, x, y - 1, 30)
    b = scene.place(UnitType.LIGHT, x + 1, y, 60)
    c = scene.place(UnitType.LIGHT, x, y + 1, 10)
    actions = {"player_0": {a: move(DOWN), b: move(LEFT), c: move(UP)}, "player_1": {}}

    def check(tally):
        if not tally.get("quirk1"):
            return "the quirk didn't happen"
        if not scene.alive(b) or scene.alive(a) or scene.alive(c):
            return "the strongest light should be the only survivor"
        return None

    return actions, check


def lights_tie(scene):
    """Two lights with the same power enter one tile: both are destroyed."""
    x, y = scene.x, scene.y
    a = scene.place(UnitType.LIGHT, x - 1, y, 40)
    b = scene.place(UnitType.LIGHT, x + 1, y, 40)
    actions = {"player_0": {a: move(RIGHT), b: move(LEFT)}, "player_1": {}}

    def check(tally):
        if not tally.get("tie") or scene.alive(a) or scene.alive(b):
            return "both lights should be destroyed in a tie"
        return None

    return actions, check


def heavies_tie(scene):
    """Two heavies with the same power enter one tile: both are destroyed."""
    x, y = scene.x, scene.y
    a = scene.place(UnitType.HEAVY, x, y - 1, 300)
    b = scene.place(UnitType.HEAVY, x, y + 1, 300)
    actions = {"player_0": {a: move(DOWN), b: move(UP)}, "player_1": {}}

    def check(tally):
        if not tally.get("tie") or scene.alive(a) or scene.alive(b):
            return "both heavies should be destroyed in a tie"
        return None

    return actions, check


def light_beats_two_heavies(scene):
    """A factory builds a heavy under the heavy standing on its center while a light
    enters it: neither heavy counts as standing, so the light survives."""
    x, y = scene.x, scene.y
    scene.factory.cargo.metal = 200
    scene.factory.power = 1000
    heavy = scene.place(UnitType.HEAVY, x, y, 500)
    light = scene.place(UnitType.LIGHT, x + 1, y, 50)
    actions = {"player_0": {light: move(LEFT), scene.factory.unit_id: 1}, "player_1": {}}

    def check(tally):
        if not tally.get("quirk2"):
            return "the quirk didn't happen"
        if not scene.alive(light) or scene.alive(heavy):
            return "the light should survive and the heavy be destroyed"
        return None

    return actions, check


def two_standing_lights(scene):
    """A factory builds a light under the light standing on its center, and nobody
    enters: both are destroyed."""
    x, y = scene.x, scene.y
    scene.factory.cargo.metal = 200
    scene.factory.power = 1000
    light = scene.place(UnitType.LIGHT, x, y, 50)
    actions = {"player_0": {scene.factory.unit_id: 0}, "player_1": {}}

    def check(tally):
        if not tally.get("quirk3"):
            return "the quirk didn't happen"
        if scene.alive(light):
            return "the standing light should be destroyed"
        return None

    return actions, check


def heavy_and_light_standing(scene):
    """A factory builds a light under the heavy standing on its center: the heavy
    survives, with no power loss."""
    x, y = scene.x, scene.y
    scene.factory.cargo.metal = 200
    scene.factory.power = 1000
    heavy = scene.place(UnitType.HEAVY, x, y, 500)
    actions = {"player_0": {scene.factory.unit_id: 0}, "player_1": {}}

    def check(tally):
        if not scene.alive(heavy) or len(scene.env.state.units["player_0"]) != 1:
            return "only the heavy should be left"
        return None

    return actions, check


def dead_robot_transfers(scene):
    """A light standing next to the factory, told to give its ice to the factory, is
    crushed by a heavy entering its tile. Its transfer still happens."""
    x, y = scene.x, scene.y
    light = scene.place(UnitType.LIGHT, x + 2, y, 50, ice=40)
    heavy = scene.place(UnitType.HEAVY, x + 2, y - 1, 1000)
    give = np.array([[1, LEFT, 0, 40, 0, 1]], dtype=np.int64)
    actions = {"player_0": {light: give, heavy: move(DOWN)}, "player_1": {}}

    def check(tally):
        if not tally.get("quirk4_transfer") or scene.alive(light):
            return "the light should be destroyed and still transfer"
        return None

    return actions, check


def dead_robot_picks_up(scene):
    """A light on the factory's edge, told to pick up power, is crushed by a heavy
    entering its tile. Its pickup still takes the factory's power."""
    x, y = scene.x, scene.y
    light = scene.place(UnitType.LIGHT, x + 1, y, 50)
    heavy = scene.place(UnitType.HEAVY, x + 2, y, 1000)
    take = np.array([[2, 0, 4, 100, 0, 1]], dtype=np.int64)
    actions = {"player_0": {light: take, heavy: move(LEFT)}, "player_1": {}}

    def check(tally):
        if not tally.get("quirk4_pickup") or scene.alive(light):
            return "the light should be destroyed and still pick up"
        return None

    return actions, check


def watering_checks_last_turns_cost(scene):
    """The watering check reads the lichen count cache_water_info left last turn
    (here 30 tiles: cost 3), not this turn's (at most the 12 starting tiles: cost
    at most 2). With 2 water, Python drops the order, though this turn's cost fits."""
    factory = scene.factory
    factory.grow_lichen_positions = {(0, i) for i in range(30)}
    factory.cargo.water = 2
    actions = {"player_0": {factory.unit_id: 2}, "player_1": {}}

    def check(tally):
        fresh_cost = -(-len(factory.grow_lichen_positions) // 10)
        if not tally.get("factory_water_dropped") or fresh_cost > 2:
            return "the order should be dropped although this turn's cost fits"
        return None

    return actions, check


def dig_out_last_lichen(scene):
    """A light digs the last lichen off a tile: the tile gets DIG_RESOURCE_GAIN (2)
    rubble, not a rubble constant."""
    x, y = scene.x + 2, scene.y
    scene.clear_tile(x, y)
    board = scene.env.state.board
    board.lichen[x, y] = 5
    board.lichen_strains[x, y] = int(scene.factory.num_id)
    light = scene.place(UnitType.LIGHT, x, y, 50)
    actions = {"player_0": {light: np.array([[3, 0, 0, 0, 0, 1]], dtype=np.int64)}, "player_1": {}}

    def check(tally):
        if board.rubble[x, y] != 2 or board.lichen[x, y] != 0:
            return f"the tile should have 2 rubble and no lichen, has {board.rubble[x, y]} and {board.lichen[x, y]}"
        return None

    return actions, check


def move_cost_before_digs(scene):
    """A light digs rubble (40 -> 38) off the tile another light enters the same
    turn. The mover pays for 40 rubble (validation runs before digging):
    floor(1 + 0.05 * 40) = 3, not floor(1 + 0.05 * 38) = 2. It then crushes the digger."""
    x, y = scene.x + 2, scene.y
    scene.clear_tile(x, y)
    scene.clear_tile(x + 1, y)
    scene.env.state.board.rubble[x, y] = 40
    digger = scene.place(UnitType.LIGHT, x, y, 50)
    mover = scene.place(UnitType.LIGHT, x + 1, y, 50)
    dig = np.array([[3, 0, 0, 0, 0, 1]], dtype=np.int64)
    actions = {"player_0": {digger: dig, mover: move(LEFT)}, "player_1": {}}

    def check(tally):
        # 50 - 1 (new queue) - 3 (move) + 1 (daytime charge)
        if scene.alive(digger) or not scene.alive(mover) or scene.power(mover) != 47:
            return "the mover should survive having paid 3 power to move"
        return None

    return actions, check


def move_onto_enemy_factory(scene):
    """A light next to an enemy factory tries to step onto it: validation drops the
    move, so it stays where it is and pays nothing for it."""
    size = scene.env.state.env_cfg.map_size
    ex, ey = int(scene.enemy.pos.x), int(scene.enemy.pos.y)
    x, direction = (ex + 2, LEFT) if ex + 2 < size else (ex - 2, RIGHT)
    if scene.env.state.board.factory_occupancy_map[x, ey] != -1:
        raise RuntimeError("the tile next to the enemy factory is another factory's")
    scene.clear_tile(x, ey)
    light = scene.place(UnitType.LIGHT, x, ey, 50)
    actions = {"player_0": {light: move(direction)}, "player_1": {}}

    def check(tally):
        unit = scene.env.state.units["player_0"].get(light)
        if not tally.get("move_dropped") or unit is None or (int(unit.pos.x), int(unit.pos.y)) != (x, ey):
            return "the move should be dropped and the light stay put"
        return None

    return actions, check


def dig_into_full_hold(scene):
    """A light with 99 of its 100 cargo in ice digs ice: it gains 1, not
    DIG_RESOURCE_GAIN (2); the rest is lost."""
    x, y = scene.x + 2, scene.y
    scene.clear_tile(x, y)
    scene.env.state.board.ice[x, y] = 1
    light = scene.place(UnitType.LIGHT, x, y, 50, ice=99)
    actions = {"player_0": {light: np.array([[3, 0, 0, 0, 0, 1]], dtype=np.int64)}, "player_1": {}}

    def check(tally):
        held = int(scene.env.state.units["player_0"][light].cargo.ice)
        if not tally.get("dig_full") or held != 100:
            return f"the light should dig into a full hold and hold 100 ice, holds {held}"
        return None

    return actions, check


def transfer_to_enemy(scene):
    """A light can transfer to an enemy robot next to it."""
    x, y = scene.x + 2, scene.y
    scene.clear_tile(x, y)
    scene.clear_tile(x + 1, y)
    light = scene.place(UnitType.LIGHT, x, y, 50, ice=30)
    enemy = scene.place(UnitType.LIGHT, x + 1, y, 50, player="player_1")
    give = np.array([[1, RIGHT, 0, 30, 0, 1]], dtype=np.int64)
    actions = {"player_0": {light: give}, "player_1": {}}

    def check(tally):
        received = int(scene.env.state.units["player_1"][enemy].cargo.ice)
        if received != 30:
            return f"the enemy should hold the 30 ice, holds {received}"
        return None

    return actions, check


SCENARIOS = [
    heavies_wrong_second,
    lights_wrong_second,
    heavies_tie,
    lights_tie,
    light_beats_two_heavies,
    two_standing_lights,
    heavy_and_light_standing,
    dead_robot_transfers,
    dead_robot_picks_up,
    watering_checks_last_turns_cost,
    dig_out_last_lichen,
    move_cost_before_digs,
    move_onto_enemy_factory,
    dig_into_full_hold,
    transfer_to_enemy,
]


def run_scenario(lib, ws, coverage, build, seed):
    """Plays the scenario's turn in both engines. Returns a list of problems."""
    env = harness.new_game(seed, random.Random(seed))
    if env is None:
        return ["the setup failed"]
    scene = Scene(env)
    actions, check = build(scene)

    cstate = loader.load_state(lib, env)
    try:
        diffs = loader.compare(loader.snapshot_python(env), loader.snapshot_c(cstate))
        if diffs:
            return ["the loaded C state differs from Python:"] + diffs
        before = dict(coverage.counts)
        problems = harness.step_both(lib, ws, env, cstate, actions, coverage)
        tally = {k: v - before.get(k, 0) for k, v in coverage.counts.items()}
        premise = check(tally)
        if premise:
            problems.insert(0, f"the scenario didn't set its case up in Python: {premise}")
        return problems
    finally:
        lib.lux_state_free(cstate)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Hand-built turns for rare cases, in both engines.")
    parser.add_argument("--seed", type=int, default=0, help="seed of the game each scenario starts from")
    parser.add_argument("--lib", default=luxc.LIB_PATH, help="the library to test (default: build/liblux.so)")
    args = parser.parse_args(argv)

    lib = luxc.load(args.lib)
    coverage = harness.Coverage()
    coverage.install()
    ws = lib.lux_workspace_new()
    if not ws:
        raise MemoryError("lux_workspace_new failed")
    failed = 0
    try:
        for build in SCENARIOS:
            problems = run_scenario(lib, ws, coverage, build, args.seed)
            name = build.__name__.replace("_", " ")
            if problems:
                failed += 1
                print(f"{name}: FAILED")
                for line in problems:
                    print(f"  {line}")
            else:
                print(f"{name}: same")
    finally:
        lib.lux_workspace_free(ws)
    print(f"{len(SCENARIOS) - failed} of {len(SCENARIOS)} scenarios the same in both engines")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
