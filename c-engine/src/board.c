/* board.c: map/board.py Board. */
#include "internal.h"

/* Board.get_factory_at: looks the factory id up in player_0's factories, then
 * player_1's. */
lux_factory *board_get_factory_at(lux_state *s, int64_t x, int64_t y)
{
    int64_t f_id = s->board.factory_occupancy_map[lux_tile(s, x, y)];
    if (f_id == -1) {
        return NULL;
    }
    int32_t i = lux_find_factory(s, 0, f_id);
    if (i >= 0) {
        return &s->factories[0][i];
    }
    i = lux_find_factory(s, 1, f_id);
    /* Python: state.factories["player_1"][unit_id] raises KeyError. check_state
     * matched the map to the factories, and only refining destroys factories. */
    assert(i >= 0);
    return &s->factories[1][i];
}

/* At the end of every turn Board.units_map maps each unit's tile to a list holding
 * just that unit: _handle_movement_actions rebuilt it from the survivors, and
 * nothing after that adds or removes units. So it is rebuilt here from the units. */
int board_build_units_map(const lux_state *s, lux_workspace *ws)
{
    for (int32_t team = 0; team < 2; team++) {
        for (int32_t i = 0; i < s->n_units[team]; i++) {
            const lux_unit *u = &s->units[team][i];
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
