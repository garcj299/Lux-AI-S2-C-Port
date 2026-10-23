"""errors: lux_step's error contract.

    errors.py [--seed S] [--lib PATH]

lux_step is all or nothing: every error is found before anything changes. Each
case here copies a real game into C, breaks one thing (an invariant every Python
state keeps between turns, an argument, the config), steps, and checks the code
returned and that not one byte of the state changed. The unbroken game must step
with LUX_OK, so each refusal is down to its break. Last, a normal turn on the same
workspace must still match Python: a refused step leaves nothing behind.
"""
import argparse
import ctypes
import random
import sys

import numpy as np

import harness
import loader
import luxc
from luxai_s2.actions import format_action_vec
from luxai_s2.unit import UnitType


def base_game(seed):
    """A game just past setup, with a light on each team's first two factory
    centers (one of them with an order queued), as the cases need units."""
    env = harness.new_game(seed, random.Random(seed))
    if env is None:
        raise SystemExit(f"seed {seed}: the setup failed; try another --seed")
    for player in loader.PLAYERS:
        factories = list(env.state.factories[player].values())
        if len(factories) < 2:
            raise SystemExit(f"seed {seed}: {player} has fewer than 2 factories; try another --seed")
        for f in factories[:2]:
            env.add_unit(env.state.teams[player], UnitType.LIGHT, f.pos.pos)
    first = next(iter(env.state.units["player_0"].values()))
    first.action_queue = [format_action_vec(np.array(row)) for row in ([0, 1, 0, 0, 0, 1], [5, 0, 0, 30, 0, 1])]
    return env


def commands(entries=((), ())):
    """(luxc.AgentCommands * 2) from (kind, id, factory_action, rows) entries per
    team, written directly, so the cases can say what no actions dict can."""
    keep = []
    agents = (luxc.AgentCommands * 2)()
    for t, team in enumerate(entries):
        array = (luxc.Command * max(len(team), 1))()
        keep.append(array)
        for i, (kind, id_, factory_action, rows) in enumerate(team):
            array[i].kind, array[i].id, array[i].factory_action = kind, id_, factory_action
            if rows is not None:
                rows = np.ascontiguousarray(np.asarray(rows, dtype=np.int64).reshape(-1, 6))
                keep.append(rows)
                array[i].n_actions = len(rows)
                array[i].actions = rows.ctypes.data_as(ctypes.POINTER(luxc.Action))
        agents[t].commands, agents[t].n_commands = array, len(team)
    return agents, keep


def tile(cs, x, y):
    return x * cs.env_cfg.map_size + y


def free_tile(cs):
    """A tile with no factory and no unit."""
    n = cs.env_cfg.map_size
    taken = {(u.x, u.y) for t in range(2) for u in cs.units[t][: cs.n_units[t]]}
    for x in range(n):
        for y in range(n):
            if cs.board.factory_occupancy_map[tile(cs, x, y)] == -1 and (x, y) not in taken:
                return x, y
    raise RuntimeError("no free tile")


# Each case: (name, expected code, a function that breaks the C state `cs` and
# returns the commands to step with, or None for no orders).


def unit_case(name, change):
    def apply(cs):
        change(cs, cs.units[0][0], cs.units[0][1])

    return name, luxc.ERR_STATE, apply


def factory_case(name, change):
    def apply(cs):
        change(cs, cs.factories[0][0], cs.factories[0][1])

    return name, luxc.ERR_STATE, apply


def set_tile(cs, x, y, value):
    cs.board.factory_occupancy_map[tile(cs, x, y)] = value


CASES = [
    # the units
    unit_case("a unit off the map", lambda cs, a, b: setattr(a, "x", cs.env_cfg.map_size)),
    unit_case("a unit at x = -1", lambda cs, a, b: setattr(a, "x", -1)),
    unit_case("two units on one tile", lambda cs, a, b: (setattr(b, "x", a.x), setattr(b, "y", a.y))),
    unit_case("units out of id order", lambda cs, a, b: (setattr(a, "unit_id", b.unit_id + 1))),
    unit_case("two units with one id", lambda cs, a, b: setattr(b, "unit_id", a.unit_id)),
    unit_case("a unit id not given out yet", lambda cs, a, b: setattr(b, "unit_id", cs.global_id)),
    unit_case("a negative unit id", lambda cs, a, b: setattr(a, "unit_id", -1)),
    unit_case("an unknown unit type", lambda cs, a, b: setattr(a, "unit_type", 2)),
    unit_case("a unit on the wrong team", lambda cs, a, b: setattr(a, "team_id", 1)),
    unit_case("a queue longer than UNIT_ACTION_QUEUE_SIZE", lambda cs, a, b: setattr(a, "queue_len", 21)),
    unit_case("a negative queue length", lambda cs, a, b: setattr(a, "queue_len", -1)),
    unit_case("a queued action off the action space", lambda cs, a, b: setattr(a.action_queue[1], "direction", 5)),
    unit_case("a queued action with n = 0", lambda cs, a, b: setattr(a.action_queue[0], "n", 0)),
    unit_case("a negative unit count", lambda cs, a, b: cs.n_units.__setitem__(1, -1)),
    unit_case("more units than tiles", lambda cs, a, b: cs.n_units.__setitem__(1, luxc.MAX_TILES + 1)),
    # the factories and the occupancy map
    factory_case("a factory id not on its tiles", lambda cs, f, g: set_tile(cs, f.x + 1, f.y, g.num_id)),
    factory_case("a factory tile left empty", lambda cs, f, g: set_tile(cs, f.x - 1, f.y - 1, -1)),
    factory_case("a tile naming no factory", lambda cs, f, g: set_tile(cs, *free_tile(cs), 999999)),
    factory_case("a stray tile of a real factory", lambda cs, f, g: set_tile(cs, *free_tile(cs), f.num_id)),
    factory_case("a factory with a queued action", lambda cs, f, g: (setattr(f, "queue_len", 1), f.action_queue.__setitem__(0, 2))),
    factory_case("factories out of id order", lambda cs, f, g: setattr(g, "num_id", f.num_id)),
    factory_case("a factory id not given out yet", lambda cs, f, g: setattr(g, "num_id", cs.global_id)),
    factory_case("a factory on the wrong team", lambda cs, f, g: setattr(f, "team_id", 1)),
    factory_case("a factory off the map", lambda cs, f, g: setattr(f, "x", cs.env_cfg.map_size)),
    factory_case("more factories than LUX_MAX_FACTORIES_PER_TEAM", lambda cs, f, g: cs.n_factories.__setitem__(0, 11)),
    factory_case("more strains than LUX_MAX_FACTORIES_PER_TEAM", lambda cs, f, g: setattr(cs.teams[0], "n_factory_strains", 11)),
    # the arguments
    ("a negative command count", luxc.ERR_ARGUMENT, lambda cs: commands_with(lambda a: setattr(a[0], "n_commands", -1))),
    ("commands missing", luxc.ERR_ARGUMENT, lambda cs: commands_with(lambda a: (setattr(a[1], "commands", None), setattr(a[1], "n_commands", 1)))),
    ("a negative action count", luxc.ERR_ARGUMENT, lambda cs: bad_rows(cs, -1)),
    ("actions missing", luxc.ERR_ARGUMENT, lambda cs: bad_rows(cs, 2)),
    ("an unknown key kind", luxc.ERR_ARGUMENT, lambda cs: commands(([(7, 0, 0, None)], ()))),
    ("a unit named twice", luxc.ERR_ARGUMENT, lambda cs: named_twice(luxc.KEY_UNIT, cs.units[0][0].unit_id)),
    ("a factory named twice", luxc.ERR_ARGUMENT, lambda cs: named_twice(luxc.KEY_FACTORY, cs.factories[0][0].num_id)),
    # the game and the config
    ("a game that has ended", luxc.ERR_GAME_OVER, lambda cs: setattr(cs, "done", 1)),
    ("a turn of the setup phase", luxc.ERR_EARLY_GAME, lambda cs: setattr(cs, "env_steps", 1)),
    ("turn 0", luxc.ERR_EARLY_GAME, lambda cs: setattr(cs, "env_steps", 0)),
    ("a map larger than LUX_MAX_MAP_SIZE", luxc.ERR_CONFIG, lambda cs: setattr(cs.env_cfg, "map_size", 65)),
    ("validate_action_space off", luxc.ERR_CONFIG, lambda cs: setattr(cs.env_cfg, "validate_action_space", 0)),
    ("BIDDING_SYSTEM off", luxc.ERR_CONFIG, lambda cs: setattr(cs.env_cfg, "BIDDING_SYSTEM", 0)),
    ("a negative RUBBLE_AFTER_DESTRUCTION", luxc.ERR_CONFIG,
     lambda cs: setattr(cs.env_cfg.ROBOTS[luxc.HEAVY], "RUBBLE_AFTER_DESTRUCTION", -1)),
    ("a zero CYCLE_LENGTH", luxc.ERR_CONFIG, lambda cs: setattr(cs.env_cfg, "CYCLE_LENGTH", 0)),
    ("a zero LICHEN_WATERING_COST_FACTOR", luxc.ERR_CONFIG, lambda cs: setattr(cs.env_cfg, "LICHEN_WATERING_COST_FACTOR", 0)),
]


def commands_with(change):
    agents, keep = commands()
    change(agents)
    return agents, keep


def bad_rows(cs, n_actions):
    """A unit command whose n_actions disagrees with its (missing) rows."""
    agents, keep = commands(([(luxc.KEY_UNIT, cs.units[0][0].unit_id, 0, None)], ()))
    agents[0].commands[0].n_actions = n_actions
    return agents, keep


def named_twice(kind, id_):
    rows = None if kind == luxc.KEY_FACTORY else [[0, 0, 0, 0, 0, 1]]
    return commands(([(kind, id_, 2, rows), (kind, id_, 2, rows)], ()))


def run_case(lib, ws, env, expected, apply):
    """Returns None if the step returned `expected` and, for an error, left the
    state as it was; else what went wrong."""
    ptr = loader.load_state(lib, env)
    try:
        given = apply(ptr.contents)
        agents, keep = given if given is not None else commands()
        before = ctypes.string_at(ptr, ctypes.sizeof(luxc.State))
        rc = lib.lux_step(ptr, ws, agents, luxc.StepResult())
        del keep
        if rc != expected:
            return f"returned {luxc.error_name(rc)}, not {luxc.error_name(expected)}"
        if expected != luxc.OK and ctypes.string_at(ptr, ctypes.sizeof(luxc.State)) != before:
            return "changed the state"
        return None
    finally:
        lib.lux_state_free(ptr)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check lux_step's error contract.")
    parser.add_argument("--seed", type=int, default=0, help="seed of the game the cases start from")
    parser.add_argument("--lib", default=luxc.LIB_PATH, help="the library to test (default: build/liblux.so)")
    args = parser.parse_args(argv)
    lib = luxc.load(args.lib)
    coverage = harness.Coverage()
    ws = lib.lux_workspace_new()
    if not ws:
        raise MemoryError("lux_workspace_new failed")
    failures = []
    try:
        env = base_game(args.seed)
        problem = run_case(lib, ws, env, luxc.OK, lambda cs: None)
        if problem:
            raise SystemExit(f"the unbroken game doesn't step: {problem}")
        for name, expected, apply in CASES:
            problem = run_case(lib, ws, env, expected, apply)
            print(f"{name}: {'FAILED, ' + problem if problem else 'refused, state untouched'}")
            if problem:
                failures.append(name)

        for null in range(4):
            ptr = loader.load_state(lib, env)
            try:
                agents, keep = commands()
                call = [ptr, ws, agents, luxc.StepResult()]
                call[null] = None
                rc = lib.lux_step(*call)
            finally:
                lib.lux_state_free(ptr)
            name = f"a NULL {['state', 'workspace', 'commands', 'result'][null]}"
            print(f"{name}: {'refused' if rc == luxc.ERR_ARGUMENT else 'FAILED, returned ' + luxc.error_name(rc)}")
            if rc != luxc.ERR_ARGUMENT:
                failures.append(name)
        bad = loader.config_from_python(env.state.env_cfg)
        bad.map_size = 65
        if lib.lux_state_new(bad):
            failures.append("lux_state_new took a bad config")

        # the workspace after all those refusals still steps a real turn right
        cstate = loader.load_state(lib, env)
        try:
            unit_id = next(iter(env.state.units["player_0"]))
            actions = {"player_0": {unit_id: np.array([[0, 2, 0, 0, 0, 1]], dtype=np.int64)}, "player_1": {}}
            diffs = harness.step_both(lib, ws, env, cstate, actions, coverage)
        finally:
            lib.lux_state_free(cstate)
        print(f"a turn after the refusals: {'FAILED' if diffs else 'same in both engines'}")
        if diffs:
            failures.append("a turn after the refusals")
            for line in diffs:
                print(f"  {line}")
    finally:
        lib.lux_workspace_free(ws)
    total = len(CASES) + 4 + 2
    print(f"{total - len(failures)} of {total} error checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
