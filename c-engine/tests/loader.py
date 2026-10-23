"""Moves a game between the Python engine and the C engine, and compares them.

- snapshot_python(env) and snapshot_c(state): the whole game in one plain form,
  the same for both engines, so they can be compared field by field.
- load_state(lib, env): a new C state holding a copy of a live Python game, which
  is snapshot_python(env) written into C (the inverse of snapshot_c).
- compare(py, c): where two snapshots differ.
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
INT64_MAX = 2**63 - 1


def entity_number(key, prefix):
    """N if key is exactly prefix followed by N, written as Python writes ints and
    small enough for an int64 ("unit_12", not "unit_012"); else None."""
    if not key.startswith(prefix):
        return None
    digits = key[len(prefix) :]
    if not re.fullmatch(r"0|[1-9][0-9]*", digits) or int(digits) > INT64_MAX:
        return None
    return int(digits)


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


def load_state(lib, env):
    """A new C state (a ctypes pointer, freed with lib.lux_state_free) holding a copy
    of env's game. snapshot_python reads everything lux.h says a state holds,
    including each factory's lichen counts, which only the live factory objects
    carry; this writes it into C."""
    cfg = config_from_python(env.state.env_cfg)
    if lib.lux_config_check(cfg) != luxc.OK:
        raise ValueError("this config is outside what the C engine supports")
    ptr = lib.lux_state_new(cfg)
    if not ptr:
        raise MemoryError("lux_state_new failed")
    try:
        write_snapshot(ptr, snapshot_python(env))
    except BaseException:
        lib.lux_state_free(ptr)
        raise
    return ptr


def write_snapshot(ptr, snap):
    """Writes a snapshot into a C state: the inverse of snapshot_c."""
    cs = ptr.contents
    n = cs.env_cfg.map_size
    cs.env_steps = snap["env_steps"]
    cs.global_id = snap["global_id"]
    cs.done = int(snap["done"])
    cs.board.factories_per_team = snap["board"]["factories_per_team"]
    for name in (*luxc.GRIDS, "valid_spawns_mask"):
        np.ctypeslib.as_array(getattr(cs.board, name))[: n * n] = snap["board"][name].reshape(-1)
    for t in range(2):
        team, ct = snap["teams"][t], cs.teams[t]
        for field in ("team_id", "faction", "init_water", "init_metal", "factories_to_place", "bid"):
            setattr(ct, field, team[field])
        ct.place_first = int(team["place_first"])
        _fill(ct.factory_strains, team["factory_strains"], "factory_strains")
        ct.n_factory_strains = len(team["factory_strains"])

        _check_fits(snap["units"][t], luxc.MAX_UNITS_PER_TEAM, "units")
        cs.n_units[t] = len(snap["units"][t])
        for cu, unit in zip(cs.units[t], snap["units"][t]):
            cu.unit_id, cu.team_id, cu.unit_type, cu.power = unit["unit_id"], unit["team_id"], unit["unit_type"], unit["power"]
            cu.x, cu.y = unit["pos"]
            cu.ice, cu.ore, cu.water, cu.metal = unit["cargo"]
            _fill(cu.action_queue, [luxc.Action(*a) for a in unit["action_queue"]], "a unit's queue")
            cu.queue_len = len(unit["action_queue"])

        _check_fits(snap["factories"][t], luxc.MAX_FACTORIES_PER_TEAM, "factories")
        cs.n_factories[t] = len(snap["factories"][t])
        for cf, factory in zip(cs.factories[t], snap["factories"][t]):
            cf.num_id, cf.team_id, cf.power = factory["num_id"], factory["team_id"], factory["power"]
            cf.x, cf.y = factory["pos"]
            cf.ice, cf.ore, cf.water, cf.metal = factory["cargo"]
            _fill(cf.action_queue, factory["action_queue"], "a factory's queue")
            cf.queue_len = len(factory["action_queue"])
            cf.grow_lichen_count = factory["grow_lichen_count"]
            cf.connected_lichen_count = factory["connected_lichen_count"]


def _check_fits(values, capacity, what):
    if len(values) > capacity:
        raise ValueError(f"{len(values)} {what} don't fit the C engine's {capacity}")


def _fill(array, values, what):
    _check_fits(values, len(array), what)
    for k, value in enumerate(values):
        array[k] = value


# ---- Actions ----------------------------------------------------------------------


def _key_to_command(key):
    """kind and id of an actions-dict key, as lux.h's lux_command defines them."""
    if not isinstance(key, str):
        raise TypeError(f"the C engine takes only string keys, not {key!r}")
    if "factory" in key:
        kind, prefix = luxc.KEY_FACTORY, "factory_"
    elif "unit" in key:
        kind, prefix = luxc.KEY_UNIT, "unit_"
    else:
        return luxc.KEY_OTHER, -1
    number = entity_number(key, prefix)
    return kind, -1 if number is None else number


def _unit_rows(key, value):
    """A unit's actions as an int64 (rows, 6) array. Python takes other forms too
    (lists, 1-D arrays, floats) and treats them in ways a lux_command can't say, so
    those raise TypeError: the caller must not send them (see lux.h)."""
    if isinstance(value, np.ndarray) and value.size == 0 and value.ndim <= 2:
        return np.zeros((0, 6), dtype=np.int64)
    if not (isinstance(value, np.ndarray) and value.ndim == 2 and value.shape[1] == 6):
        raise TypeError(f"{key}: the C engine takes unit actions as (n, 6) arrays only, not {value!r}")
    try:
        return np.ascontiguousarray(value.astype(np.int64, casting="safe"))
    except TypeError:
        raise TypeError(f"{key}: the C engine takes integer actions only, not {value.dtype}") from None


def _factory_action(key, value):
    if not isinstance(value, (int, np.integer)) or np.ndim(value) != 0 or not -(2**63) <= int(value) <= INT64_MAX:
        raise TypeError(f"{key}: the C engine takes a factory action as one int64, not {value!r}")
    return int(value)


def commands_from_actions(actions):
    """The actions dict as (luxc.AgentCommands * 2), plus the arrays it points
    into, which must stay alive until lux_step returns."""
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
                c.factory_action = _factory_action(key, value)
            elif c.kind == luxc.KEY_UNIT:
                rows = _unit_rows(key, value)
                keep.append(rows)
                c.n_actions = len(rows)
                c.actions = rows.ctypes.data_as(POINTER(luxc.Action)) if len(rows) else None
        agents[t].commands = commands
        agents[t].n_commands = len(entries)
    return agents, keep


# ---- Snapshots ----------------------------------------------------------------------


def snapshot_python(env):
    """The whole Python game, in the form snapshot_c gives the C one."""
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
                    "unit_id": _number(u.unit_id, "unit_"),
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
                    "num_id": _number(f.unit_id, "factory_", int(f.num_id)),
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


def _number(entity_id, prefix, expected=None):
    """N of the ids the engine makes, "unit_N" and "factory_N"."""
    number = entity_number(entity_id, prefix)
    if number is None or expected not in (None, number):
        raise ValueError(f"{entity_id} isn't an id the engine makes")
    return number


def snapshot_c(ptr):
    """The whole C game, in the form snapshot_python gives the Python one."""
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
        ids = [_number(u.unit_id, "unit_") for u in state.units[player].values()]
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
