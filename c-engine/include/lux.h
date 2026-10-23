/*
 * lux.h: the C port of the Lux AI Season 2 engine (luxai_s2 3.0.1).
 *
 * The rules are translated function by function from the Python engine in
 * .venv/lib/python3.14/site-packages/luxai_s2/. Each C function names the Python
 * function it translates. Field names follow the Python attributes, so the two
 * can be read side by side. See ../README.md for the design and the quirks kept.
 *
 * Scope so far: the turns of the main game (real_env_steps >= 0). The setup phase
 * (bid and factory placement) and the map generator are not ported: a state is
 * loaded already past the setup phase, by filling a lux_state.
 */
#ifndef LUX_H
#define LUX_H

#include <stdint.h>

/* The library exports only the functions marked LUX_API (it is built with
 * -fvisibility=hidden). */
#if defined(__GNUC__)
#define LUX_API __attribute__((visibility("default")))
#else
#define LUX_API
#endif

/* ---- Capacities ---------------------------------------------------------- */

#define LUX_MAX_MAP_SIZE 64
#define LUX_MAX_TILES (LUX_MAX_MAP_SIZE * LUX_MAX_MAP_SIZE)

/* EnvConfig.MAX_FACTORIES: a team places at most factories_per_team factories. */
#define LUX_MAX_FACTORIES_PER_TEAM 10

/* At the end of every turn there is at most one unit per tile, and during a turn
 * each factory builds at most one more. So this bound can never be reached. */
#define LUX_MAX_UNITS_PER_TEAM (LUX_MAX_TILES + LUX_MAX_FACTORIES_PER_TEAM)

/* EnvConfig.UNIT_ACTION_QUEUE_SIZE. */
#define LUX_MAX_QUEUE 20

/* A factory's queue gets at most one action per turn and loses it the same turn,
 * unless the team failed, which ends the game. So it never holds more than 2. */
#define LUX_FACTORY_QUEUE_CAP 4

/* ---- Return codes ----------------------------------------------------------- */

enum {
    LUX_OK = 0,
    LUX_ERR_ARGUMENT = -1,   /* NULL pointer, negative count or a repeated key */
    LUX_ERR_GAME_OVER = -2,  /* the game has ended (Python's step would raise) */
    LUX_ERR_EARLY_GAME = -3, /* bid or factory placement: not ported yet */
    LUX_ERR_CONFIG = -4,     /* a config this port doesn't support (see lux_config_check) */
    LUX_ERR_STATE = -5,      /* the state breaks an invariant every reachable Python state keeps */
    LUX_ERR_CAPACITY = -6,   /* a fixed array would overflow; impossible within the bounds above */
    LUX_ERR_NO_MEMORY = -7
};

/* ---- Enumerations --------------------------------------------------------- */

/* unit.py UnitType, and the keys of EnvConfig.ROBOTS. */
enum { LUX_LIGHT = 0, LUX_HEAVY = 1 };

/* team.py FactionTypes, by faction_id. */
enum { LUX_ALPHA_STRIKE = 0, LUX_MOTHER_MARS = 1, LUX_THE_BUILDERS = 2, LUX_FIRST_MARS = 3 };

/* actions.py: a[0] of a unit action. */
enum {
    LUX_MOVE = 0,
    LUX_TRANSFER = 1,
    LUX_PICKUP = 2,
    LUX_DIG = 3,
    LUX_SELF_DESTRUCT = 4,
    LUX_RECHARGE = 5
};

/* actions.py format_factory_action: the factory action codes. */
enum { LUX_BUILD_LIGHT = 0, LUX_BUILD_HEAVY = 1, LUX_WATER = 2 };

/* Directions, a[1]: 0 center, 1 up, 2 right, 3 down, 4 left. */
/* Resources, a[2]: 0 ice, 1 ore, 2 water, 3 metal, 4 power. */

/* ---- Configuration (config.py) ------------------------------------------ */

typedef struct {
    int64_t METAL_COST;
    int64_t POWER_COST;
    int64_t CARGO_SPACE;
    int64_t BATTERY_CAPACITY;
    int64_t CHARGE;
    int64_t INIT_POWER;
    int64_t MOVE_COST;
    double RUBBLE_MOVEMENT_COST;
    int64_t DIG_COST;
    int64_t DIG_RUBBLE_REMOVED;
    int64_t DIG_RESOURCE_GAIN;
    int64_t DIG_LICHEN_REMOVED;
    int64_t SELF_DESTRUCT_COST;
    int64_t RUBBLE_AFTER_DESTRUCTION;
    int64_t ACTION_QUEUE_POWER_COST;
} lux_unit_config;

typedef struct {
    int64_t max_episode_length;
    int64_t map_size;
    int64_t verbose; /* kept for completeness: the C engine prints nothing */
    int32_t validate_action_space;
    int32_t BIDDING_SYSTEM;
    int64_t max_transfer_amount;
    int64_t MIN_FACTORIES;
    int64_t MAX_FACTORIES;
    int64_t CYCLE_LENGTH;
    int64_t DAY_LENGTH;
    int64_t UNIT_ACTION_QUEUE_SIZE;
    int64_t MAX_RUBBLE;
    int64_t FACTORY_RUBBLE_AFTER_DESTRUCTION;
    int64_t INIT_WATER_METAL_PER_FACTORY;
    int64_t INIT_POWER_PER_FACTORY;
    int64_t MIN_LICHEN_TO_SPREAD;
    int64_t LICHEN_LOST_WITHOUT_WATER; /* unused by the rules, as in Python (decay is a literal 1) */
    int64_t LICHEN_GAINED_WITH_WATER;  /* unused by the rules, as in Python (watering is a literal 2) */
    int64_t MAX_LICHEN_PER_TILE;
    int64_t POWER_PER_CONNECTED_LICHEN_TILE;
    int64_t LICHEN_WATERING_COST_FACTOR;
    int64_t FACTORY_PROCESSING_RATE_WATER;
    int64_t ICE_WATER_RATIO;
    int64_t FACTORY_PROCESSING_RATE_METAL;
    int64_t ORE_METAL_RATIO;
    int64_t FACTORY_CHARGE;
    int64_t FACTORY_WATER_CONSUMPTION;
    double POWER_LOSS_FACTOR;
    lux_unit_config ROBOTS[2]; /* [LUX_LIGHT], [LUX_HEAVY] */
} lux_config;

/* EnvConfig(): the defaults of luxai_s2 3.0.1. */
LUX_API void lux_config_default(lux_config *cfg);

/* LUX_OK, or LUX_ERR_CONFIG for a config this port doesn't support: map_size
 * above LUX_MAX_MAP_SIZE, more factories or a longer queue than the capacities,
 * a zero divisor, or validate_action_space off (without that check, Python lets
 * out-of-range numbers into the queues, then crashes on some and wraps others). */
LUX_API int lux_config_check(const lux_config *cfg);

/* ---- Game state ------------------------------------------------------------- */

/* One action as its six numbers, the layout of the action's state_dict(). In a
 * queue the fields an action type doesn't use are 0, as format_action_vec leaves
 * them. Laid out as int64[6], so a NumPy row maps onto it directly. */
typedef struct {
    int64_t act_type;  /* a[0] */
    int64_t direction; /* a[1] */
    int64_t resource;  /* a[2] */
    int64_t amount;    /* a[3]: transfer or pickup amount, or the power a recharge waits for */
    int64_t repeat;    /* a[4] */
    int64_t n;         /* a[5] */
} lux_action;

/* unit.py Unit. The unit's string id is "unit_<unit_id>". */
typedef struct {
    int64_t unit_id;
    int32_t team_id;
    int32_t unit_type; /* LUX_LIGHT or LUX_HEAVY */
    int64_t x, y;      /* pos */
    int64_t power;
    int64_t ice, ore, water, metal; /* cargo */
    int32_t queue_len;
    int32_t reserved_; /* keeps the layout free of hidden padding */
    lux_action action_queue[LUX_MAX_QUEUE];
} lux_unit;

/* factory.py Factory. Its string id is "factory_<num_id>"; num_id is also its
 * lichen strain id and the value it leaves in factory_occupancy_map. */
typedef struct {
    int64_t num_id;
    int32_t team_id;
    int32_t queue_len;
    int64_t x, y; /* pos: the center of its 3x3 tiles */
    int64_t power;
    int64_t ice, ore, water, metal; /* cargo */
    int64_t action_queue[LUX_FACTORY_QUEUE_CAP]; /* LUX_BUILD_LIGHT, LUX_BUILD_HEAVY or LUX_WATER */
    /* len(grow_lichen_positions) and len(connected_lichen_positions) as left by the
     * last cache_water_info. The next turn's watering check reads the old count. */
    int64_t grow_lichen_count;
    int64_t connected_lichen_count;
} lux_factory;

/* team.py Team. */
typedef struct {
    int32_t team_id;
    int32_t faction; /* faction_id */
    int64_t init_water;
    int64_t init_metal;
    int64_t factories_to_place;
    int64_t bid;
    int32_t place_first;
    int32_t n_factory_strains;
    /* Every factory the team ever placed, destroyed ones included: their lichen still scores. */
    int64_t factory_strains[LUX_MAX_FACTORIES_PER_TEAM];
} lux_team;

/* map/board.py Board. Grids are map_size x map_size, indexed [x][y] as the Python
 * engine indexes its arrays: tile (x, y) is element x * map_size + y, so a NumPy
 * array's row-major data copies in unchanged. Tiles past map_size are unused. */
typedef struct {
    int64_t factories_per_team;
    int64_t rubble[LUX_MAX_TILES];
    int64_t ore[LUX_MAX_TILES];
    int64_t ice[LUX_MAX_TILES];
    int64_t lichen[LUX_MAX_TILES];
    int64_t lichen_strains[LUX_MAX_TILES];        /* -1 = nobody's */
    int64_t factory_occupancy_map[LUX_MAX_TILES]; /* -1 = no factory, else its num_id */
    uint8_t valid_spawns_mask[LUX_MAX_TILES];     /* used only by factory placement */
} lux_board;

/* state/state.py State, plus the env attributes the rules read.
 *
 * Units and factories are kept per team in the order Python's OrderedDicts keep
 * them: creation order, which is also increasing id order. The units map
 * (Board.units_map) isn't stored: at the end of every turn it holds exactly the
 * units, at most one per tile, so each turn rebuilds it from them.
 *
 * Not kept, because the rules never read them: seed, seed_rng and the map
 * generator's GameMap (setup only), stats (collect_stats), Board.factory_map
 * (a cache keyed by position), Board.units_map (rebuilt, see above). */
typedef struct {
    lux_config env_cfg;
    int64_t env_steps;
    int64_t global_id; /* the next unit or factory gets this id */
    int32_t n_units[2];
    int32_t n_factories[2];
    int32_t done;      /* the game has ended: Python's env.agents is [] */
    int32_t reserved_;
    lux_team teams[2]; /* [0] = player_0, [1] = player_1 */
    lux_board board;
    lux_factory factories[2][LUX_MAX_FACTORIES_PER_TEAM];
    lux_unit units[2][LUX_MAX_UNITS_PER_TEAM];
} lux_state;

/* A zeroed state on the heap, with env_cfg = *cfg and the board as
 * Board.post_map_gen leaves it apart from the map: lichen 0, lichen_strains and
 * factory_occupancy_map -1, valid_spawns_mask 1. NULL if cfg fails
 * lux_config_check or memory runs out. The caller fills in the rest. */
LUX_API lux_state *lux_state_new(const lux_config *cfg);
LUX_API void lux_state_free(lux_state *state);

/* state.py State.real_env_steps: env_steps minus the bid and placement turns. */
LUX_API int64_t lux_real_env_steps(const lux_state *state);

/* The sizes and offsets of this header's types, for bindings in other languages
 * to check their copy of the layout against. Writes up to n values to out, in
 * this order, and returns how many there are:
 *   sizeof lux_config, lux_unit_config, lux_action, lux_unit, lux_factory,
 *   lux_team, lux_board, lux_state, lux_command, lux_agent_commands,
 *   lux_step_result; offsetof lux_config.ROBOTS, lux_unit.action_queue,
 *   lux_state.teams, lux_state.board, lux_state.factories, lux_state.units. */
LUX_API int32_t lux_layout(int64_t *out, int32_t n);

/* ---- Actions for one turn ----------------------------------------------------- */

/* Which branch a key of the actions dict takes in Python's step(): a key holding
 * "factory" is a factory, else one holding "unit" is a unit, else it is ignored
 * (and fails the action-space check). */
enum { LUX_KEY_UNIT = 0, LUX_KEY_FACTORY = 1, LUX_KEY_OTHER = 2 };

/* One entry of a player's actions dict, in the dict's order.
 *
 * id is N when the key is exactly "unit_N" or "factory_N" (N written as Python
 * writes it), and -1 for any other key. A key that names no unit or factory of
 * this team is handled as Python handles it: the team fails, and the entries
 * after it are skipped.
 *
 * Python-level type errors (a 1-D action array, floats, strings, missing keys)
 * can't be written here. Telling those apart is the Python caller's job. */
typedef struct {
    int32_t kind;
    int32_t n_actions;          /* LUX_KEY_UNIT: rows in actions; may exceed the queue size */
    int64_t id;
    int64_t factory_action;     /* LUX_KEY_FACTORY */
    const lux_action *actions;  /* LUX_KEY_UNIT: the raw rows, unformatted */
} lux_command;

/* A player's actions dict. An empty dict and a missing player behave the same. */
typedef struct {
    const lux_command *commands;
    int32_t n_commands;
} lux_agent_commands;

typedef struct {
    int64_t rewards[2]; /* each team's lichen, or -1000 if it failed */
    int32_t failed[2];  /* failed_agents */
    int32_t done;       /* env_done; both players' terminations equal it */
} lux_step_result;

/* ---- Stepping ------------------------------------------------------------------- */

/* Scratch memory for lux_step: one per thread, reusable across states and games. */
typedef struct lux_workspace lux_workspace;
LUX_API lux_workspace *lux_workspace_new(void);
LUX_API void lux_workspace_free(lux_workspace *ws);

/* env.py LuxAI_S2.step for one main-game turn: commands[0] are player_0's actions
 * and commands[1] player_1's.
 *
 * LUX_ERR_ARGUMENT, LUX_ERR_GAME_OVER, LUX_ERR_EARLY_GAME, LUX_ERR_CONFIG and an
 * LUX_ERR_STATE found by the opening checks leave the state untouched. An
 * LUX_ERR_STATE or LUX_ERR_CAPACITY found later leaves it half-stepped, as the
 * matching Python exception would. */
LUX_API int lux_step(lux_state *state, lux_workspace *ws, const lux_agent_commands commands[2],
             lux_step_result *result);

#endif /* LUX_H */
