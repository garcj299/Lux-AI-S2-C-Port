/* board.c: map/board.py Board. */
#include "internal.h"

/* Board.get_factory_at: looks the factory id up in player_0's factories, then
 * player_1's. */
lux_factory *board_get_factory_at(lux_state *s, int64_t x, int64_t y, int *err)
{
    int64_t f_id = s->board.factory_occupancy_map[lux_tile(s, x, y)];
    if (f_id != -1) {
        int32_t i = lux_find_factory(s, 0, f_id);
        if (i >= 0) {
            return &s->factories[0][i];
        }
        i = lux_find_factory(s, 1, f_id);
        if (i >= 0) {
            return &s->factories[1][i];
        }
        *err = LUX_ERR_STATE; /* state.factories["player_1"][unit_id] raises KeyError */
    }
    return NULL;
}

/* At the end of every turn Board.units_map maps each unit's tile to a list holding
 * just that unit: _handle_movement_actions rebuilt it from the survivors, and
 * nothing after that adds or removes units. So it is rebuilt here from the units.
 * Also checks what the rest of the step relies on. */
int board_build_units_map(const lux_state *s, lux_workspace *ws)
{
    for (int32_t t = 0; t < LUX_MAX_TILES; t++) {
        ws->umap_n[t] = 0;
    }
    for (int32_t team = 0; team < 2; team++) {
        if (s->n_units[team] < 0 || s->n_units[team] > LUX_MAX_UNITS_PER_TEAM) {
            return LUX_ERR_STATE;
        }
        for (int32_t i = 0; i < s->n_units[team]; i++) {
            const lux_unit *u = &s->units[team][i];
            if (u->team_id != team || (u->unit_type != LUX_LIGHT && u->unit_type != LUX_HEAVY)) {
                return LUX_ERR_STATE;
            }
            /* OrderedDict order is creation order, which is id order */
            if (i > 0 && s->units[team][i - 1].unit_id >= u->unit_id) {
                return LUX_ERR_STATE;
            }
            if (u->queue_len < 0 || u->queue_len > LUX_MAX_QUEUE) {
                return LUX_ERR_STATE;
            }
            if (!lux_in_bounds(s, u->x, u->y)) {
                return LUX_ERR_STATE;
            }
            int32_t tile = lux_tile(s, u->x, u->y);
            if (ws->umap_n[tile] != 0) {
                return LUX_ERR_STATE;
            }
            ws->umap[tile][0] = (lux_ref){team, i};
            ws->umap_n[tile] = 1;
        }
    }
    return LUX_OK;
}
