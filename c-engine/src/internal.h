/*
 * internal.h: what the engine's modules share. Each module is named after the
 * Python file it translates.
 */
#ifndef LUX_INTERNAL_H
#define LUX_INTERNAL_H

#include <stddef.h>

#include "lux.h"
#include "pymath.h"

/* A unit or a factory: state->units[team][index] or state->factories[team][index].
 * A unit's index stays valid for a whole lux_step: units destroyed during the turn
 * are only marked dead, and removed when the step ends. */
typedef struct {
    int32_t team;
    int32_t index;
} lux_ref;

/* The lists of env.py's ActionsByType. The first six are the unit action types,
 * so a unit action's act_type is its list. */
enum {
    ACT_MOVE = LUX_MOVE,
    ACT_TRANSFER = LUX_TRANSFER,
    ACT_PICKUP = LUX_PICKUP,
    ACT_DIG = LUX_DIG,
    ACT_SELF_DESTRUCT = LUX_SELF_DESTRUCT,
    ACT_RECHARGE = LUX_RECHARGE,
    ACT_FACTORY_BUILD,
    ACT_FACTORY_WATER,
    ACT_N
};

/* One (unit or factory, action) pair of ActionsByType. */
typedef struct {
    lux_ref who;
    lux_action *action; /* units: the front of their queue, the object the handlers update */
    int32_t unit_type;  /* factory_build: FactoryBuildAction.unit_type */
    int32_t reserved_;
    int64_t power_cost; /* Action.power_cost, as validate_actions sets it */
} lux_act;

/* Board.units_map holds at most one unit per tile at the start of a turn, plus
 * the unit a factory builds on its center during the turn. */
#define LUX_UMAP_CAP 2
/* At most 4 units enter a tile (one from each side) and 2 stay on it. */
#define LUX_ENTER_CAP 4
#define LUX_NEW_MAP_CAP (LUX_ENTER_CAP + LUX_UMAP_CAP)
/* compute_water_info starts from 12 tiles, then adds each tile at most once. */
#define LUX_FRONTIER_CAP (12 + LUX_MAX_TILES)

struct lux_workspace {
    /* ActionsByType */
    lux_act acts[ACT_N][2 * LUX_MAX_UNITS_PER_TEAM];
    int32_t n_acts[ACT_N];

    /* Units destroyed this turn: Python deletes them from state.units at once, but
     * the transfer and pickup handlers still run their actions afterwards. */
    uint8_t dead[2][LUX_MAX_UNITS_PER_TEAM];

    /* Board.units_map: the units on each tile, in list order */
    lux_ref umap[LUX_MAX_TILES][LUX_UMAP_CAP];
    int32_t umap_n[LUX_MAX_TILES];

    /* _handle_movement_actions */
    lux_ref new_map[LUX_MAX_TILES][LUX_NEW_MAP_CAP];
    int32_t new_map_n[LUX_MAX_TILES];
    lux_ref heavy_entered[LUX_MAX_TILES][LUX_ENTER_CAP];
    int32_t heavy_entered_n[LUX_MAX_TILES];
    lux_ref light_entered[LUX_MAX_TILES][LUX_ENTER_CAP];
    int32_t light_entered_n[LUX_MAX_TILES];
    int32_t touched[LUX_MAX_TILES]; /* the tiles that are keys of new_units_map */
    int32_t n_touched;
    uint8_t is_touched[LUX_MAX_TILES];
    uint8_t merged[LUX_MAX_TILES]; /* stationary units already added for this tile */
    lux_ref survivor[LUX_MAX_TILES];
    uint8_t has_survivor[LUX_MAX_TILES];
    lux_ref destroyed[2 * LUX_MAX_UNITS_PER_TEAM]; /* all_destroyed_units, deduplicated */
    int32_t n_destroyed;
    uint8_t in_destroyed[2][LUX_MAX_UNITS_PER_TEAM];

    /* compute_water_info */
    int64_t frontier_x[LUX_FRONTIER_CAP];
    int64_t frontier_y[LUX_FRONTIER_CAP];
    uint32_t seen_stamp[LUX_MAX_TILES]; /* a tile is in `seen` when its stamp is seen_gen */
    uint32_t seen_gen;
    /* each factory's grow_lichen_positions, as tile indices, from cache_water_info
     * until _handle_factory_water_actions uses them */
    int32_t grow_tiles[2][LUX_MAX_FACTORIES_PER_TEAM][LUX_MAX_TILES];

    /* _handle_transfer_actions: what each transfer took from its unit */
    int64_t amount_list[2 * LUX_MAX_UNITS_PER_TEAM];

    /* the refining loop's factories_to_destroy */
    uint8_t factory_destroy[LUX_MAX_FACTORIES_PER_TEAM];

    /* lux_step's argument check: units and factories already named by a command */
    uint8_t named_unit[2][LUX_MAX_UNITS_PER_TEAM];
    uint8_t named_factory[2][LUX_MAX_FACTORIES_PER_TEAM];
};

/* tile (x, y) of the [x][y]-indexed grids */
static inline int32_t lux_tile(const lux_state *s, int64_t x, int64_t y)
{
    return (int32_t)(x * s->env_cfg.map_size + y);
}

static inline int lux_in_bounds(const lux_state *s, int64_t x, int64_t y)
{
    return x >= 0 && y >= 0 && x < s->env_cfg.map_size && y < s->env_cfg.map_size;
}

static inline lux_unit *lux_unit_at(lux_state *s, lux_ref r)
{
    return &s->units[r.team][r.index];
}

/* ---- state.c ---- */
/* The index of the team's unit with this id, or -1. Units are sorted by id. */
int32_t lux_find_unit(const lux_state *s, int32_t team, int64_t unit_id);
/* The index of the team's factory with this num_id, or -1. */
int32_t lux_find_factory(const lux_state *s, int32_t team, int64_t num_id);

/* ---- unit.py ---- */
void unit_init(lux_unit *u, const lux_config *cfg, int32_t team_id, int32_t unit_type, int64_t unit_id);
void unit_repeat_action(lux_unit *u, lux_action *action);
int64_t unit_move_power_cost(const lux_config *cfg, const lux_unit *u, int64_t rubble_at_target);
int64_t unit_add_resource(const lux_config *cfg, lux_unit *u, int64_t resource_id, int64_t amount);
int64_t unit_sub_resource(lux_unit *u, int64_t resource_id, int64_t amount);

/* ---- factory.py ---- */
void factory_pos_slice(const lux_state *s, const lux_factory *f, int64_t *x_lo, int64_t *x_hi,
                       int64_t *y_lo, int64_t *y_hi);
void factory_refine_step(lux_factory *f, const lux_config *cfg);
void factory_cache_water_info(const lux_state *s, lux_factory *f, lux_workspace *ws, int32_t *grow_tiles);
int64_t factory_water_cost(const lux_factory *f, const lux_config *cfg);
int64_t factory_add_resource(lux_factory *f, int64_t resource_id, int64_t transfer_amount);
int64_t factory_sub_resource(lux_factory *f, int64_t resource_id, int64_t amount);

/* ---- map/board.py ---- */
/* Board.get_factory_at: the factory covering (x, y), or NULL if none. Sets *err to
 * LUX_ERR_STATE when the map names a factory neither team has (Python: KeyError). */
lux_factory *board_get_factory_at(lux_state *s, int64_t x, int64_t y, int *err);
/* Board.units_map, rebuilt from the units: LUX_ERR_STATE if a unit is off the map,
 * two share a tile, or units aren't sorted by id within their team. */
int board_build_units_map(const lux_state *s, lux_workspace *ws);

/* ---- utils/utils.py ---- */
int is_day(const lux_config *cfg, int64_t env_step);
void get_top_two_power_units(lux_state *s, const lux_ref *units, int32_t n_units, int32_t unit_type,
                             lux_ref *most_power_unit, lux_ref *next_most_power_unit);

/* ---- actions.py ---- */
extern const int64_t lux_move_deltas[5][2];
int format_action_vec(const lux_action *a, lux_action *act);
int format_factory_action(int64_t a);
int validate_actions(lux_state *s, lux_workspace *ws);
/* Every action in a live game's queue passed the action-space check when it was
 * given. 1 if this one is inside those ranges. */
int action_in_space(const lux_config *cfg, const lux_action *a);

/* ---- spaces/act_space.py ---- */
int act_space_contains(const lux_state *s, int32_t team, const lux_agent_commands *cmds);

#endif /* LUX_INTERNAL_H */
