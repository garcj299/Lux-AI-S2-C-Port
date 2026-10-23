"""ctypes bindings for the C engine: c-engine/include/lux.h, field for field.

load() checks these structures against the sizes and offsets the library reports
(lux_layout), so a change to lux.h that isn't copied here fails at once instead of
corrupting memory.
"""
import ctypes
from ctypes import POINTER, Structure, c_double, c_int32, c_int64, c_uint8, c_void_p
from pathlib import Path

LIB_PATH = Path(__file__).resolve().parent.parent / "build" / "liblux.so"

MAX_MAP_SIZE = 64
MAX_TILES = MAX_MAP_SIZE * MAX_MAP_SIZE
MAX_FACTORIES_PER_TEAM = 10
MAX_UNITS_PER_TEAM = MAX_TILES + MAX_FACTORIES_PER_TEAM
MAX_QUEUE = 20
FACTORY_QUEUE_CAP = 4

OK = 0
ERRORS = {
    -1: "LUX_ERR_ARGUMENT",
    -2: "LUX_ERR_GAME_OVER",
    -3: "LUX_ERR_EARLY_GAME",
    -4: "LUX_ERR_CONFIG",
    -5: "LUX_ERR_STATE",
    -6: "LUX_ERR_CAPACITY",
    -7: "LUX_ERR_NO_MEMORY",
}

LIGHT, HEAVY = 0, 1
KEY_UNIT, KEY_FACTORY, KEY_OTHER = 0, 1, 2


class UnitConfig(Structure):
    _fields_ = [
        ("METAL_COST", c_int64),
        ("POWER_COST", c_int64),
        ("CARGO_SPACE", c_int64),
        ("BATTERY_CAPACITY", c_int64),
        ("CHARGE", c_int64),
        ("INIT_POWER", c_int64),
        ("MOVE_COST", c_int64),
        ("RUBBLE_MOVEMENT_COST", c_double),
        ("DIG_COST", c_int64),
        ("DIG_RUBBLE_REMOVED", c_int64),
        ("DIG_RESOURCE_GAIN", c_int64),
        ("DIG_LICHEN_REMOVED", c_int64),
        ("SELF_DESTRUCT_COST", c_int64),
        ("RUBBLE_AFTER_DESTRUCTION", c_int64),
        ("ACTION_QUEUE_POWER_COST", c_int64),
    ]


class Config(Structure):
    _fields_ = [
        ("max_episode_length", c_int64),
        ("map_size", c_int64),
        ("verbose", c_int64),
        ("validate_action_space", c_int32),
        ("BIDDING_SYSTEM", c_int32),
        ("max_transfer_amount", c_int64),
        ("MIN_FACTORIES", c_int64),
        ("MAX_FACTORIES", c_int64),
        ("CYCLE_LENGTH", c_int64),
        ("DAY_LENGTH", c_int64),
        ("UNIT_ACTION_QUEUE_SIZE", c_int64),
        ("MAX_RUBBLE", c_int64),
        ("FACTORY_RUBBLE_AFTER_DESTRUCTION", c_int64),
        ("INIT_WATER_METAL_PER_FACTORY", c_int64),
        ("INIT_POWER_PER_FACTORY", c_int64),
        ("MIN_LICHEN_TO_SPREAD", c_int64),
        ("LICHEN_LOST_WITHOUT_WATER", c_int64),
        ("LICHEN_GAINED_WITH_WATER", c_int64),
        ("MAX_LICHEN_PER_TILE", c_int64),
        ("POWER_PER_CONNECTED_LICHEN_TILE", c_int64),
        ("LICHEN_WATERING_COST_FACTOR", c_int64),
        ("FACTORY_PROCESSING_RATE_WATER", c_int64),
        ("ICE_WATER_RATIO", c_int64),
        ("FACTORY_PROCESSING_RATE_METAL", c_int64),
        ("ORE_METAL_RATIO", c_int64),
        ("FACTORY_CHARGE", c_int64),
        ("FACTORY_WATER_CONSUMPTION", c_int64),
        ("POWER_LOSS_FACTOR", c_double),
        ("ROBOTS", UnitConfig * 2),
    ]


class Action(Structure):
    _fields_ = [
        ("act_type", c_int64),
        ("direction", c_int64),
        ("resource", c_int64),
        ("amount", c_int64),
        ("repeat", c_int64),
        ("n", c_int64),
    ]


class Unit(Structure):
    _fields_ = [
        ("unit_id", c_int64),
        ("team_id", c_int32),
        ("unit_type", c_int32),
        ("x", c_int64),
        ("y", c_int64),
        ("power", c_int64),
        ("ice", c_int64),
        ("ore", c_int64),
        ("water", c_int64),
        ("metal", c_int64),
        ("queue_len", c_int32),
        ("reserved_", c_int32),
        ("action_queue", Action * MAX_QUEUE),
    ]


class Factory(Structure):
    _fields_ = [
        ("num_id", c_int64),
        ("team_id", c_int32),
        ("queue_len", c_int32),
        ("x", c_int64),
        ("y", c_int64),
        ("power", c_int64),
        ("ice", c_int64),
        ("ore", c_int64),
        ("water", c_int64),
        ("metal", c_int64),
        ("action_queue", c_int64 * FACTORY_QUEUE_CAP),
        ("grow_lichen_count", c_int64),
        ("connected_lichen_count", c_int64),
    ]


class Team(Structure):
    _fields_ = [
        ("team_id", c_int32),
        ("faction", c_int32),
        ("init_water", c_int64),
        ("init_metal", c_int64),
        ("factories_to_place", c_int64),
        ("bid", c_int64),
        ("place_first", c_int32),
        ("n_factory_strains", c_int32),
        ("factory_strains", c_int64 * MAX_FACTORIES_PER_TEAM),
    ]


GRIDS = ("rubble", "ore", "ice", "lichen", "lichen_strains", "factory_occupancy_map")


class Board(Structure):
    _fields_ = [
        ("factories_per_team", c_int64),
        *[(name, c_int64 * MAX_TILES) for name in GRIDS],
        ("valid_spawns_mask", c_uint8 * MAX_TILES),
    ]


class State(Structure):
    _fields_ = [
        ("env_cfg", Config),
        ("env_steps", c_int64),
        ("global_id", c_int64),
        ("n_units", c_int32 * 2),
        ("n_factories", c_int32 * 2),
        ("done", c_int32),
        ("reserved_", c_int32),
        ("teams", Team * 2),
        ("board", Board),
        ("factories", (Factory * MAX_FACTORIES_PER_TEAM) * 2),
        ("units", (Unit * MAX_UNITS_PER_TEAM) * 2),
    ]


class Command(Structure):
    _fields_ = [
        ("kind", c_int32),
        ("n_actions", c_int32),
        ("id", c_int64),
        ("factory_action", c_int64),
        ("actions", POINTER(Action)),
    ]


class AgentCommands(Structure):
    _fields_ = [
        ("commands", POINTER(Command)),
        ("n_commands", c_int32),
    ]


class StepResult(Structure):
    _fields_ = [
        ("rewards", c_int64 * 2),
        ("failed", c_int32 * 2),
        ("done", c_int32),
    ]


def _expected_layout():
    """What lux_layout must report, in its order."""
    return [
        ctypes.sizeof(Config),
        ctypes.sizeof(UnitConfig),
        ctypes.sizeof(Action),
        ctypes.sizeof(Unit),
        ctypes.sizeof(Factory),
        ctypes.sizeof(Team),
        ctypes.sizeof(Board),
        ctypes.sizeof(State),
        ctypes.sizeof(Command),
        ctypes.sizeof(AgentCommands),
        ctypes.sizeof(StepResult),
        Config.ROBOTS.offset,
        Unit.action_queue.offset,
        State.teams.offset,
        State.board.offset,
        State.factories.offset,
        State.units.offset,
    ]


def load(path=LIB_PATH):
    """The library, with its functions typed and its layout checked against this file."""
    if not Path(path).exists():
        raise SystemExit(f"{path} not found: run make in c-engine/ first")
    lib = ctypes.CDLL(str(path))

    lib.lux_config_default.argtypes = [POINTER(Config)]
    lib.lux_config_default.restype = None
    lib.lux_config_check.argtypes = [POINTER(Config)]
    lib.lux_config_check.restype = ctypes.c_int
    lib.lux_state_new.argtypes = [POINTER(Config)]
    lib.lux_state_new.restype = POINTER(State)
    lib.lux_state_free.argtypes = [POINTER(State)]
    lib.lux_state_free.restype = None
    lib.lux_real_env_steps.argtypes = [POINTER(State)]
    lib.lux_real_env_steps.restype = c_int64
    lib.lux_layout.argtypes = [POINTER(c_int64), c_int32]
    lib.lux_layout.restype = c_int32
    lib.lux_workspace_new.argtypes = []
    lib.lux_workspace_new.restype = c_void_p
    lib.lux_workspace_free.argtypes = [c_void_p]
    lib.lux_workspace_free.restype = None
    lib.lux_step.argtypes = [POINTER(State), c_void_p, POINTER(AgentCommands), POINTER(StepResult)]
    lib.lux_step.restype = ctypes.c_int

    expected = _expected_layout()
    reported = (c_int64 * len(expected))()
    count = lib.lux_layout(reported, len(expected))
    if count != len(expected) or list(reported) != expected:
        raise SystemExit(
            f"luxc.py doesn't match lux.h: the library reports {list(reported)[:count]} "
            f"({count} values), these bindings expect {expected}"
        )
    return lib


def error_name(code):
    return ERRORS.get(code, f"error {code}")
