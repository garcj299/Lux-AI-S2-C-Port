/* state.c: state/state.py State, and the memory the engine runs in. */
#include <stdlib.h>
#include <string.h>

#include "internal.h"

lux_state *lux_state_new(const lux_config *cfg)
{
    if (lux_config_check(cfg) != LUX_OK) {
        return NULL;
    }
    lux_state *s = calloc(1, sizeof(*s));
    if (s == NULL) {
        return NULL;
    }
    s->env_cfg = *cfg;
    /* Board.post_map_gen, apart from the map itself */
    for (int32_t t = 0; t < LUX_MAX_TILES; t++) {
        s->board.lichen_strains[t] = -1;
        s->board.factory_occupancy_map[t] = -1;
        s->board.valid_spawns_mask[t] = 1;
    }
    s->teams[0].team_id = 0;
    s->teams[1].team_id = 1;
    return s;
}

void lux_state_free(lux_state *state)
{
    free(state);
}

int64_t lux_real_env_steps(const lux_state *state)
{
    if (state->env_cfg.BIDDING_SYSTEM) {
        /* + 1 for bidding step, * 2 for total factories placed by two teams */
        return state->env_steps - (state->board.factories_per_team * 2 + 1);
    }
    return state->env_steps;
}

/* ---- lux_layout --------------------------------------------------------------- */

#define SIZE(T) {"sizeof(" #T ")", (int64_t)sizeof(T)}
#define FIELD(T, f) {"offsetof(" #T ", " #f ")", (int64_t)offsetof(T, f)}, {"sizeof(" #T "." #f ")", (int64_t)sizeof(((T *)0)->f)}
#define CONSTANT(c) {#c, (int64_t)(c)}

static const lux_layout_entry layout[] = {
    SIZE(lux_unit_config),
    FIELD(lux_unit_config, METAL_COST),
    FIELD(lux_unit_config, POWER_COST),
    FIELD(lux_unit_config, CARGO_SPACE),
    FIELD(lux_unit_config, BATTERY_CAPACITY),
    FIELD(lux_unit_config, CHARGE),
    FIELD(lux_unit_config, INIT_POWER),
    FIELD(lux_unit_config, MOVE_COST),
    FIELD(lux_unit_config, RUBBLE_MOVEMENT_COST),
    FIELD(lux_unit_config, DIG_COST),
    FIELD(lux_unit_config, DIG_RUBBLE_REMOVED),
    FIELD(lux_unit_config, DIG_RESOURCE_GAIN),
    FIELD(lux_unit_config, DIG_LICHEN_REMOVED),
    FIELD(lux_unit_config, SELF_DESTRUCT_COST),
    FIELD(lux_unit_config, RUBBLE_AFTER_DESTRUCTION),
    FIELD(lux_unit_config, ACTION_QUEUE_POWER_COST),

    SIZE(lux_config),
    FIELD(lux_config, max_episode_length),
    FIELD(lux_config, map_size),
    FIELD(lux_config, verbose),
    FIELD(lux_config, validate_action_space),
    FIELD(lux_config, BIDDING_SYSTEM),
    FIELD(lux_config, max_transfer_amount),
    FIELD(lux_config, MIN_FACTORIES),
    FIELD(lux_config, MAX_FACTORIES),
    FIELD(lux_config, CYCLE_LENGTH),
    FIELD(lux_config, DAY_LENGTH),
    FIELD(lux_config, UNIT_ACTION_QUEUE_SIZE),
    FIELD(lux_config, MAX_RUBBLE),
    FIELD(lux_config, FACTORY_RUBBLE_AFTER_DESTRUCTION),
    FIELD(lux_config, INIT_WATER_METAL_PER_FACTORY),
    FIELD(lux_config, INIT_POWER_PER_FACTORY),
    FIELD(lux_config, MIN_LICHEN_TO_SPREAD),
    FIELD(lux_config, LICHEN_LOST_WITHOUT_WATER),
    FIELD(lux_config, LICHEN_GAINED_WITH_WATER),
    FIELD(lux_config, MAX_LICHEN_PER_TILE),
    FIELD(lux_config, POWER_PER_CONNECTED_LICHEN_TILE),
    FIELD(lux_config, LICHEN_WATERING_COST_FACTOR),
    FIELD(lux_config, FACTORY_PROCESSING_RATE_WATER),
    FIELD(lux_config, ICE_WATER_RATIO),
    FIELD(lux_config, FACTORY_PROCESSING_RATE_METAL),
    FIELD(lux_config, ORE_METAL_RATIO),
    FIELD(lux_config, FACTORY_CHARGE),
    FIELD(lux_config, FACTORY_WATER_CONSUMPTION),
    FIELD(lux_config, POWER_LOSS_FACTOR),
    FIELD(lux_config, ROBOTS),

    SIZE(lux_action),
    FIELD(lux_action, act_type),
    FIELD(lux_action, direction),
    FIELD(lux_action, resource),
    FIELD(lux_action, amount),
    FIELD(lux_action, repeat),
    FIELD(lux_action, n),

    SIZE(lux_unit),
    FIELD(lux_unit, unit_id),
    FIELD(lux_unit, team_id),
    FIELD(lux_unit, unit_type),
    FIELD(lux_unit, x),
    FIELD(lux_unit, y),
    FIELD(lux_unit, power),
    FIELD(lux_unit, ice),
    FIELD(lux_unit, ore),
    FIELD(lux_unit, water),
    FIELD(lux_unit, metal),
    FIELD(lux_unit, queue_len),
    FIELD(lux_unit, reserved_),
    FIELD(lux_unit, action_queue),

    SIZE(lux_factory),
    FIELD(lux_factory, num_id),
    FIELD(lux_factory, team_id),
    FIELD(lux_factory, queue_len),
    FIELD(lux_factory, x),
    FIELD(lux_factory, y),
    FIELD(lux_factory, power),
    FIELD(lux_factory, ice),
    FIELD(lux_factory, ore),
    FIELD(lux_factory, water),
    FIELD(lux_factory, metal),
    FIELD(lux_factory, action_queue),
    FIELD(lux_factory, grow_lichen_count),
    FIELD(lux_factory, connected_lichen_count),

    SIZE(lux_team),
    FIELD(lux_team, team_id),
    FIELD(lux_team, faction),
    FIELD(lux_team, init_water),
    FIELD(lux_team, init_metal),
    FIELD(lux_team, factories_to_place),
    FIELD(lux_team, bid),
    FIELD(lux_team, place_first),
    FIELD(lux_team, n_factory_strains),
    FIELD(lux_team, factory_strains),

    SIZE(lux_board),
    FIELD(lux_board, factories_per_team),
    FIELD(lux_board, rubble),
    FIELD(lux_board, ore),
    FIELD(lux_board, ice),
    FIELD(lux_board, lichen),
    FIELD(lux_board, lichen_strains),
    FIELD(lux_board, factory_occupancy_map),
    FIELD(lux_board, valid_spawns_mask),

    SIZE(lux_state),
    FIELD(lux_state, env_cfg),
    FIELD(lux_state, env_steps),
    FIELD(lux_state, global_id),
    FIELD(lux_state, n_units),
    FIELD(lux_state, n_factories),
    FIELD(lux_state, done),
    FIELD(lux_state, reserved_),
    FIELD(lux_state, teams),
    FIELD(lux_state, board),
    FIELD(lux_state, factories),
    FIELD(lux_state, units),

    SIZE(lux_command),
    FIELD(lux_command, kind),
    FIELD(lux_command, n_actions),
    FIELD(lux_command, id),
    FIELD(lux_command, factory_action),
    FIELD(lux_command, actions),

    SIZE(lux_agent_commands),
    FIELD(lux_agent_commands, commands),
    FIELD(lux_agent_commands, n_commands),

    SIZE(lux_step_result),
    FIELD(lux_step_result, rewards),
    FIELD(lux_step_result, failed),
    FIELD(lux_step_result, done),

    SIZE(lux_layout_entry),
    FIELD(lux_layout_entry, name),
    FIELD(lux_layout_entry, value),

    CONSTANT(LUX_MAX_MAP_SIZE),
    CONSTANT(LUX_MAX_TILES),
    CONSTANT(LUX_MAX_FACTORIES_PER_TEAM),
    CONSTANT(LUX_MAX_UNITS_PER_TEAM),
    CONSTANT(LUX_MAX_QUEUE),
    CONSTANT(LUX_FACTORY_QUEUE_CAP),
    CONSTANT(LUX_OK),
    CONSTANT(LUX_ERR_ARGUMENT),
    CONSTANT(LUX_ERR_GAME_OVER),
    CONSTANT(LUX_ERR_EARLY_GAME),
    CONSTANT(LUX_ERR_CONFIG),
    CONSTANT(LUX_ERR_STATE),
    CONSTANT(LUX_LIGHT),
    CONSTANT(LUX_HEAVY),
    CONSTANT(LUX_ALPHA_STRIKE),
    CONSTANT(LUX_MOTHER_MARS),
    CONSTANT(LUX_THE_BUILDERS),
    CONSTANT(LUX_FIRST_MARS),
    CONSTANT(LUX_MOVE),
    CONSTANT(LUX_TRANSFER),
    CONSTANT(LUX_PICKUP),
    CONSTANT(LUX_DIG),
    CONSTANT(LUX_SELF_DESTRUCT),
    CONSTANT(LUX_RECHARGE),
    CONSTANT(LUX_BUILD_LIGHT),
    CONSTANT(LUX_BUILD_HEAVY),
    CONSTANT(LUX_WATER),
    CONSTANT(LUX_KEY_UNIT),
    CONSTANT(LUX_KEY_FACTORY),
    CONSTANT(LUX_KEY_OTHER),
};

int32_t lux_layout(const lux_layout_entry **entries)
{
    *entries = layout;
    return (int32_t)(sizeof(layout) / sizeof(layout[0]));
}

/* ---- The workspace ------------------------------------------------------------- */

lux_workspace *lux_workspace_new(void)
{
    lux_workspace *ws = calloc(1, sizeof(*ws));
    return ws;
}

void lux_workspace_free(lux_workspace *ws)
{
    free(ws);
}

void workspace_reset(lux_workspace *ws, const lux_state *s)
{
    /* every tile index is x * map_size + y < map_size * map_size */
    size_t n_tiles = (size_t)(s->env_cfg.map_size * s->env_cfg.map_size);
    memset(ws->n_acts, 0, sizeof(ws->n_acts));
    memset(ws->dead, 0, sizeof(ws->dead));
    memset(ws->umap_n, 0, n_tiles * sizeof(ws->umap_n[0]));
    memset(ws->new_map_n, 0, n_tiles * sizeof(ws->new_map_n[0]));
    memset(ws->heavy_entered_n, 0, n_tiles * sizeof(ws->heavy_entered_n[0]));
    memset(ws->light_entered_n, 0, n_tiles * sizeof(ws->light_entered_n[0]));
    memset(ws->named_unit, 0, sizeof(ws->named_unit));
    memset(ws->named_factory, 0, sizeof(ws->named_factory));
}

int32_t lux_find_unit(const lux_state *s, int32_t team, int64_t unit_id)
{
    int32_t lo = 0;
    int32_t hi = s->n_units[team];
    while (lo < hi) {
        int32_t mid = lo + (hi - lo) / 2;
        int64_t id = s->units[team][mid].unit_id;
        if (id == unit_id) {
            return mid;
        }
        if (id < unit_id) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    return -1;
}

int32_t lux_find_factory(const lux_state *s, int32_t team, int64_t num_id)
{
    for (int32_t i = 0; i < s->n_factories[team]; i++) {
        if (s->factories[team][i].num_id == num_id) {
            return i;
        }
    }
    return -1;
}
