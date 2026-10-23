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

lux_workspace *lux_workspace_new(void)
{
    lux_workspace *ws = calloc(1, sizeof(*ws));
    return ws;
}

void lux_workspace_free(lux_workspace *ws)
{
    free(ws);
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
