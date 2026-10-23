"""Moves a game between the Python engine and the C engine, and compares them.

- load_state(lib, env): a new C state holding a copy of a live Python game.
- snapshot_python(env) and snapshot_c(state): the whole game in one plain form,
  so the two engines can be compared field by field.
- compare(py, c): where the two snapshots differ.
- commands_from_actions(actions): a step's actions dict as the C engine takes it.
- check_python_invariants(env): what the C engine assumes about every Python
  state, checked on the real thing.
"""
import re
from ctypes import POINTER

import numpy as np

import luxc

PLAYERS = ("player_0", "player_1")
UNIT_TYPES = {"LIGHT": luxc.LIGHT, "HEAVY": luxc.HEAVY}


# ---- Python -> C ------------------------------------------------------------------


def config_from_python(env_cfg):
    """EnvConfig as a luxc.Config. The field names are the same."""
    cfg = luxc.Config()
    for name, ctype in luxc.Config._fields_:
        if name == "ROBOTS":
            continue
        value = getattr(env_cfg, name)
        setattr(cfg, name, float(value) if ctype is luxc.c_double else int(value))
    for i, key in enumerate(("LIGHT", "HEAVY")):
        unit_cfg = env_cfg.ROBOTS[key]
        for name, ctype in luxc.UnitConfig._fields_:
            value = getattr(unit_cfg, name)
            setattr(cfg.ROBOTS[i], name, float(value) if ctype is luxc.c_double else int(value))
    return cfg


def _entity_number(entity_id, prefix):
    """N of "unit_N" / "factory_N"; the engine only makes ids of this form."""
    number = int(entity_id[len(prefix) :])
    assert entity_id == f"{prefix}{number}", entity_id
    return number


def load_state(lib, env):
    """A new C state (a ctypes pointer, freed with lib.lux_state_free) holding a copy
    of env's game. Copies what lux.h says a state holds, including each factory's
    lichen counts, which only the live factory objects carry."""
    state = env.state
    cfg = config_from_python(state.env_cfg)
    if lib.lux_config_check(cfg) != luxc.OK:
        raise ValueError("this config is outside what the C engine supports")
    ptr = lib.lux_state_new(cfg)
    if not ptr:
        raise MemoryError("lux_state_new failed")
    cs = ptr.contents

    cs.env_steps = int(state.env_steps)
    cs.global_id = int(state.global_id)
    cs.done = 0 if env.agents else 1

    n = state.env_cfg.map_size
    board = state.board
    cs.board.factories_per_team = int(board.factories_per_team)
    for name in luxc.GRIDS:
        grid = np.ctypeslib.as_array(getattr(cs.board, name))
        grid[: n * n] = np.asarray(getattr(board, name), dtype=np.int64).reshape(-1)
    mask = np.ctypeslib.as_array(cs.board.valid_spawns_mask)
    mask[: n * n] = np.asarray(board.valid_spawns_mask, dtype=np.uint8).reshape(-1)

    for t, player in enumerate(PLAYERS):
        team = state.teams[player]
        ct = cs.teams[t]
        ct.team_id = team.team_id
        ct.faction = team.faction.value.faction_id
        ct.init_water = int(team.init_water)
        ct.init_metal = int(team.init_metal)
        ct.factories_to_place = int(team.factories_to_place)
        ct.bid = int(team.bid)
        ct.place_first = int(bool(team.place_first))
        ct.n_factory_strains = len(team.factory_strains)
        for k, strain in enumerate(team.factory_strains):
            ct.factory_strains[k] = int(strain)

        units = list(state.units[player].values())
        cs.n_units[t] = len(units)
        for i, unit in enumerate(units):
            cu = cs.units[t][i]
            cu.unit_id = _entity_number(unit.unit_id, "unit_")
            cu.team_id = unit.team_id
            cu.unit_type = UNIT_TYPES[unit.unit_type.name]
            cu.x, cu.y = int(unit.pos.x), int(unit.pos.y)
            cu.power = int(unit.power)
            cu.ice, cu.ore = int(unit.cargo.ice), int(unit.cargo.ore)
            cu.water, cu.metal = int(unit.cargo.water), int(unit.cargo.metal)
            cu.queue_len = len(unit.action_queue)
            for k, action in enumerate(unit.action_queue):
                values = [int(v) for v in action.state_dict()]
                cu.action_queue[k] = luxc.Action(*values)

        factories = list(state.factories[player].values())
        cs.n_factories[t] = len(factories)
        for i, factory in enumerate(factories):
            cf = cs.factories[t][i]
            cf.num_id = int(factory.num_id)
            assert factory.unit_id == f"factory_{factory.num_id}", factory.unit_id
            cf.team_id = factory.team_id
            cf.x, cf.y = int(factory.pos.x), int(factory.pos.y)
            cf.power = int(factory.power)
            cf.ice, cf.ore = int(factory.cargo.ice), int(factory.cargo.ore)
            cf.water, cf.metal = int(factory.cargo.water), int(factory.cargo.metal)
            cf.queue_len = len(factory.action_queue)
            for k, action in enumerate(factory.action_queue):
                cf.action_queue[k] = int(action.state_dict())
            cf.grow_lichen_count = len(factory.grow_lichen_positions)
            cf.connected_lichen_count = len(factory.connected_lichen_positions)
    return ptr


_CANONICAL_ID = re.compile(r"(?:unit|factory)_(0|[1-9][0-9]*)")


def _key_to_command(key):
    """kind and id of an actions-dict key, as lux.h's lux_command defines them."""
    if "factory" in key:
        kind, prefix = luxc.KEY_FACTORY, "factory_"
    elif "unit" in key:
        kind, prefix = luxc.KEY_UNIT, "unit_"
    else:
        return luxc.KEY_OTHER, -1
    match = _CANONICAL_ID.fullmatch(key)
    if match and key.startswith(prefix):
        return kind, int(match.group(1))
    return kind, -1


def commands_from_actions(actions):
    """The actions dict as (luxc.AgentCommands * 2), plus the arrays it points
    into, which must stay alive until lux_step returns. Unit actions must be 2-D
    int arrays (or lists of 6-number rows) and factory actions ints: the forms
    the C engine can take (see lux.h)."""
    keep = []
    agents = (luxc.AgentCommands * 2)()
    for t, player in enumerate(PLAYERS):
        entries = actions.get(player, {})
        commands = (luxc.Command * max(len(entries), 1))()
        keep.append(commands)
        for i, (key, value) in enumerate(entries.items()):
            c = commands[i]
            c.kind, c.id = _key_to_command(key)
            if c.kind == luxc.KEY_FACTORY:
                c.factory_action = int(value)
            elif c.kind == luxc.KEY_UNIT:
                rows = np.ascontiguousarray(np.asarray(value, dtype=np.int64).reshape(-1, 6))
                keep.append(rows)
                c.n_actions = len(rows)
                c.actions = rows.ctypes.data_as(POINTER(luxc.Action)) if len(rows) else None
        agents[t].commands = commands
        agents[t].n_commands = len(entries)
    return agents, keep


# ---- Snapshots ----------------------------------------------------------------------


def snapshot_python(env):
    state = env.state
    board = state.board
    snap = {
        "env_steps": int(state.env_steps),
        "global_id": int(state.global_id),
        "done": not env.agents,
        "board": {"factories_per_team": int(board.factories_per_team)},
        "teams": [],
        "units": [],
        "factories": [],
    }
    for name in luxc.GRIDS:
        snap["board"][name] = np.asarray(getattr(board, name), dtype=np.int64)
    snap["board"]["valid_spawns_mask"] = np.asarray(board.valid_spawns_mask, dtype=np.uint8)
    for player in PLAYERS:
        team = state.teams[player]
        snap["teams"].append(
            {
                "team_id": team.team_id,
                "faction": team.faction.value.faction_id,
                "init_water": int(team.init_water),
                "init_metal": int(team.init_metal),
                "factories_to_place": int(team.factories_to_place),
                "bid": int(team.bid),
                "place_first": bool(team.place_first),
                "factory_strains": [int(s) for s in team.factory_strains],
            }
        )
        snap["units"].append(
            [
                {
                    "unit_id": _entity_number(u.unit_id, "unit_"),
                    "team_id": u.team_id,
                    "unit_type": UNIT_TYPES[u.unit_type.name],
                    "pos": (int(u.pos.x), int(u.pos.y)),
                    "power": int(u.power),
                    "cargo": (int(u.cargo.ice), int(u.cargo.ore), int(u.cargo.water), int(u.cargo.metal)),
                    "action_queue": [tuple(int(v) for v in a.state_dict()) for a in u.action_queue],
                }
                for u in state.units[player].values()
            ]
        )
        snap["factories"].append(
            [
                {
                    "num_id": int(f.num_id),
                    "team_id": f.team_id,
                    "pos": (int(f.pos.x), int(f.pos.y)),
                    "power": int(f.power),
                    "cargo": (int(f.cargo.ice), int(f.cargo.ore), int(f.cargo.water), int(f.cargo.metal)),
                    "action_queue": [int(a.state_dict()) for a in f.action_queue],
                    "grow_lichen_count": len(f.grow_lichen_positions),
                    "connected_lichen_count": len(f.connected_lichen_positions),
                }
                for f in state.factories[player].values()
            ]
        )
    return snap


def snapshot_c(ptr):
    cs = ptr.contents
    n = cs.env_cfg.map_size
    snap = {
        "env_steps": cs.env_steps,
        "global_id": cs.global_id,
        "done": bool(cs.done),
        "board": {"factories_per_team": cs.board.factories_per_team},
        "teams": [],
        "units": [],
        "factories": [],
    }
    for name in luxc.GRIDS:
        grid = np.ctypeslib.as_array(getattr(cs.board, name))
        snap["board"][name] = grid[: n * n].reshape(n, n).copy()
    mask = np.ctypeslib.as_array(cs.board.valid_spawns_mask)
    snap["board"]["valid_spawns_mask"] = mask[: n * n].reshape(n, n).copy()
    for t in range(2):
        ct = cs.teams[t]
        snap["teams"].append(
            {
                "team_id": ct.team_id,
                "faction": ct.faction,
                "init_water": ct.init_water,
                "init_metal": ct.init_metal,
                "factories_to_place": ct.factories_to_place,
                "bid": ct.bid,
                "place_first": bool(ct.place_first),
                "factory_strains": list(ct.factory_strains[: ct.n_factory_strains]),
            }
        )
        units = []
        for i in range(cs.n_units[t]):
            u = cs.units[t][i]
            queue = [
                (a.act_type, a.direction, a.resource, a.amount, a.repeat, a.n)
                for a in u.action_queue[: u.queue_len]
            ]
            units.append(
                {
                    "unit_id": u.unit_id,
                    "team_id": u.team_id,
                    "unit_type": u.unit_type,
                    "pos": (u.x, u.y),
                    "power": u.power,
                    "cargo": (u.ice, u.ore, u.water, u.metal),
                    "action_queue": queue,
                }
            )
        snap["units"].append(units)
        factories = []
        for i in range(cs.n_factories[t]):
            f = cs.factories[t][i]
            factories.append(
                {
                    "num_id": f.num_id,
                    "team_id": f.team_id,
                    "pos": (f.x, f.y),
                    "power": f.power,
                    "cargo": (f.ice, f.ore, f.water, f.metal),
                    "action_queue": list(f.action_queue[: f.queue_len]),
                    "grow_lichen_count": f.grow_lichen_count,
                    "connected_lichen_count": f.connected_lichen_count,
                }
            )
        snap["factories"].append(factories)
    return snap


def compare(py, c, path="", limit=12):
    """The differences between two snapshots, as readable lines (at most `limit`).
    Empty if they are equal."""
    out = []

    def walk(a, b, p):
        if len(out) >= limit:
            return
        if isinstance(a, np.ndarray):
            if a.shape != b.shape:
                out.append(f"{p}: shape python {a.shape}, C {b.shape}")
                return
            for x, y in zip(*np.nonzero(a != b)):
                if len(out) >= limit:
                    return
                out.append(f"{p} at (x={x}, y={y}): python {a[x, y]}, C {b[x, y]}")
        elif isinstance(a, dict):
            for key in a:
                walk(a[key], b[key], f"{p}.{key}" if p else key)
        elif isinstance(a, list) and a and isinstance(a[0], (dict, list)):
            if len(a) != len(b):
                out.append(f"{p}: python has {len(a)}, C has {len(b)}")
                ids_py = [e.get("unit_id", e.get("num_id")) for e in a if isinstance(e, dict)]
                ids_c = [e.get("unit_id", e.get("num_id")) for e in b if isinstance(e, dict)]
                if ids_py or ids_c:
                    out.append(f"{p} ids: python {ids_py}, C {ids_c}")
                return
            for i, (ea, eb) in enumerate(zip(a, b)):
                label = ea.get("unit_id", ea.get("num_id", i)) if isinstance(ea, dict) else i
                walk(ea, eb, f"{p}[{label}]")
        elif a != b:
            out.append(f"{p}: python {a}, C {b}")

    walk(py, c, path)
    return out


# ---- Python's own invariants ---------------------------------------------------------


def check_python_invariants(env):
    """The facts about a Python state between turns that the C engine relies on (see
    lux.h and c-engine/README.md). A failure here means the C engine's design, not
    its code, is wrong."""
    state = env.state
    problems = []
    alive = {}
    for player in PLAYERS:
        ids = [_entity_number(u.unit_id, "unit_") for u in state.units[player].values()]
        if ids != sorted(set(ids)):
            problems.append(f"{player}'s units are not in increasing id order: {ids}")
        nums = [int(f.num_id) for f in state.factories[player].values()]
        if nums != sorted(set(nums)):
            problems.append(f"{player}'s factories are not in increasing id order: {nums}")
        for u in state.units[player].values():
            alive[id(u)] = u
    units_map = state.board.units_map
    on_map = 0
    for key, units in units_map.items():
        if len(units) != 1:
            problems.append(f"units_map[{key}] holds {len(units)} units")
            continue
        u = units[0]
        on_map += 1
        if id(u) not in alive:
            problems.append(f"units_map[{key}] holds {u.unit_id}, which is not alive")
        elif key != f"{u.pos.x},{u.pos.y}":
            problems.append(f"units_map[{key}] holds {u.unit_id}, which is at {u.pos.x},{u.pos.y}")
    if on_map != len(alive):
        problems.append(f"units_map holds {on_map} units, the game has {len(alive)}")
    return problems
