/* factory.c: factory.py, Factory and compute_water_info. */
#include "internal.h"

/* Factory.pos_slice: slice(x - 1, x + 2), slice(y - 1, y + 2), as the ranges those
 * slices select on the board's axes. */
void factory_pos_slice(const lux_state *s, const lux_factory *f, int64_t *x_lo, int64_t *x_hi,
                       int64_t *y_lo, int64_t *y_hi)
{
    py_slice(f->x - 1, f->x + 2, s->env_cfg.map_size, x_lo, x_hi);
    py_slice(f->y - 1, f->y + 2, s->env_cfg.map_size, y_lo, y_hi);
}

/* Factory.refine_step */
void factory_refine_step(lux_factory *f, const lux_config *cfg)
{
    int64_t max_consumed_ice = py_min(f->ice, cfg->FACTORY_PROCESSING_RATE_WATER);
    int64_t max_consumed_ore = py_min(f->ore, cfg->FACTORY_PROCESSING_RATE_METAL);
    /* permit refinement of blocks of resources, no floats. */
    int64_t produced_water = py_floordiv(max_consumed_ice, cfg->ICE_WATER_RATIO);
    int64_t produced_metal = py_floordiv(max_consumed_ore, cfg->ORE_METAL_RATIO);
    f->ice -= produced_water * cfg->ICE_WATER_RATIO;
    f->ore -= produced_metal * cfg->ORE_METAL_RATIO;

    f->water += produced_water;
    f->metal += produced_metal;
}

/* The forbidden mask of Factory.cache_water_info at one tile:
 * (rubble > 0) | (factory_occupancy_map != -1) | (ice > 0) | (ore > 0).
 * compute_water_info only reads it at in-bounds tiles, and nothing changes the
 * board while it runs, so reading the tile when needed equals building the mask first. */
static int forbidden_at(const lux_state *s, int32_t tile)
{
    const lux_board *b = &s->board;
    return b->rubble[tile] > 0 || b->factory_occupancy_map[tile] != -1 || b->ice[tile] > 0 ||
           b->ore[tile] > 0;
}

/* compute_water_info, with init = the factory's 12 starting tiles. Each popped tile
 * that can grow goes to grow_tiles; returns their number, the size of
 * grow_lichen_positions, and sets *connected to the size of connected_lichen_positions.
 *
 * Every tile enters the frontier at most once (seen), so each is popped at most
 * once and the two sets need no duplicate checks. */
static int64_t compute_water_info(const lux_state *s, lux_workspace *ws, const int64_t init[12][2],
                                  int64_t MIN_LICHEN_TO_SPREAD, int64_t strain_id, int32_t *grow_tiles,
                                  int64_t *connected)
{
    const lux_board *b = &s->board;
    const int64_t H = s->env_cfg.map_size;
    const int64_t W = s->env_cfg.map_size;

    /* a fresh, empty `seen` */
    ws->seen_gen += 1;
    if (ws->seen_gen == 0) {
        for (int32_t t = 0; t < LUX_MAX_TILES; t++) {
            ws->seen_stamp[t] = 0;
        }
        ws->seen_gen = 1;
    }

    /* frontier = deque(init); seen = set(map(tuple, init)). Only in-bounds tiles are
     * ever looked up in seen, so off-map init positions need no mark. */
    int32_t head = 0;
    int32_t tail = 0;
    for (int i = 0; i < 12; i++) {
        ws->frontier_x[tail] = init[i][0];
        ws->frontier_y[tail] = init[i][1];
        tail++;
        if (lux_in_bounds(s, init[i][0], init[i][1])) {
            ws->seen_stamp[lux_tile(s, init[i][0], init[i][1])] = ws->seen_gen;
        }
    }

    int64_t n_grow = 0;
    int64_t n_connected = 0;
    int64_t ct = 0;
    while (head < tail) {
        ct += 1;
        if (ct > 1000000) {
            /* "Error! Lichen Growth calculation took too long" (unreachable: at most
             * 12 + map tiles are ever queued) */
            break;
        }
        int64_t px = ws->frontier_x[head];
        int64_t py = ws->frontier_y[head];
        head++;
        if (px < 0 || py < 0 || px >= H || py >= W) {
            continue;
        }
        int32_t pos = lux_tile(s, px, py);
        if (forbidden_at(s, pos)) {
            continue;
        }
        int64_t pos_lichen = b->lichen[pos];
        int64_t pos_strain = b->lichen_strains[pos];
        /* check for surrounding tiles with lichen and no incompatible lichen strains, grow on those */
        int can_grow = 1;
        for (int d = 1; d < 5; d++) {
            int64_t cx = px + lux_move_deltas[d][0];
            int64_t cy = py + lux_move_deltas[d][1];
            /* check surrounding tiles on the map */
            if (cx < 0 || cy < 0 || cx >= H || cy >= W) {
                continue;
            }
            int32_t check = lux_tile(s, cx, cy);

            /* If any neighbor 1. has a different strain, or 2. is a different factory,
             * then the current pos cannot grow */
            int64_t adj_strain = b->lichen_strains[check];
            int64_t adj_factory = b->factory_occupancy_map[check];
            if ((adj_strain != -1 && adj_strain != strain_id) ||
                (adj_factory != -1 && adj_factory != strain_id)) {
                can_grow = 0;
            }

            /* if seen, skip */
            if (ws->seen_stamp[check] == ws->seen_gen) {
                continue;
            }

            /* we add it to the frontier only in two cases:
             *  1. it is an empty tile, and current pos has enough lichen to expand.
             *  2. both current tile and check_pos are of our strain. */
            if ((adj_strain == -1 && pos_lichen >= MIN_LICHEN_TO_SPREAD) ||
                (adj_strain == strain_id && pos_strain == strain_id)) {
                ws->seen_stamp[check] = ws->seen_gen;
                ws->frontier_x[tail] = cx;
                ws->frontier_y[tail] = cy;
                tail++;
            }
        }

        if (can_grow || b->lichen_strains[pos] == strain_id) {
            grow_tiles[n_grow] = pos;
            n_grow++;
            if (b->lichen_strains[pos] == strain_id) {
                n_connected++;
            }
        }
    }
    *connected = n_connected;
    return n_grow;
}

/* Factory.cache_water_info: a BFS from the 12 tiles around the factory's 3x3
 * (marked x) for the tiles its lichen can grow on.
 *
 *        x x x
 *        _ _ _
 *     x |     | x
 *     x |     | x
 *     x |_ _ _| x
 *        x x x
 */
void factory_cache_water_info(const lux_state *s, lux_factory *f, lux_workspace *ws, int32_t *grow_tiles)
{
    static const int64_t deltas[12][2] = {
        {0, -2}, {-1, -2}, {1, -2}, {0, 2}, {-1, 2}, {1, 2},
        {2, 0},  {2, -1},  {2, 1},  {-2, 0}, {-2, -1}, {-2, 1},
    };
    int64_t init[12][2];
    for (int i = 0; i < 12; i++) {
        init[i][0] = deltas[i][0] + f->x;
        init[i][1] = deltas[i][1] + f->y;
    }
    int64_t connected = 0;
    f->grow_lichen_count = compute_water_info(s, ws, (const int64_t(*)[2])init,
                                              s->env_cfg.MIN_LICHEN_TO_SPREAD, f->num_id, grow_tiles,
                                              &connected);
    f->connected_lichen_count = connected;
}

/* Factory.water_cost: int(np.ceil(len(grow_lichen_positions) / LICHEN_WATERING_COST_FACTOR)),
 * a true (float) division, as in Python. */
int64_t factory_water_cost(const lux_factory *f, const lux_config *cfg)
{
    return py_ceil((double)f->grow_lichen_count / (double)cfg->LICHEN_WATERING_COST_FACTOR);
}

/* Factory.add_resource: no capacity limit. */
int64_t factory_add_resource(lux_factory *f, int64_t resource_id, int64_t transfer_amount)
{
    if (transfer_amount < 0) {
        transfer_amount = 0;
    }
    if (resource_id == 0) {
        f->ice += transfer_amount;
    } else if (resource_id == 1) {
        f->ore += transfer_amount;
    } else if (resource_id == 2) {
        f->water += transfer_amount;
    } else if (resource_id == 3) {
        f->metal += transfer_amount;
    } else if (resource_id == 4) {
        f->power += transfer_amount;
    }
    return transfer_amount;
}

/* Factory.sub_resource: subtract/transfer out as much as you min(have, request). */
int64_t factory_sub_resource(lux_factory *f, int64_t resource_id, int64_t amount)
{
    int64_t transfer_amount = 0;
    if (amount < 0) {
        amount = 0;
    }
    if (resource_id == 0) {
        transfer_amount = py_min(f->ice, amount);
        f->ice -= transfer_amount;
    } else if (resource_id == 1) {
        transfer_amount = py_min(f->ore, amount);
        f->ore -= transfer_amount;
    } else if (resource_id == 2) {
        transfer_amount = py_min(f->water, amount);
        f->water -= transfer_amount;
    } else if (resource_id == 3) {
        transfer_amount = py_min(f->metal, amount);
        f->metal -= transfer_amount;
    } else if (resource_id == 4) {
        transfer_amount = py_min(f->power, amount);
        f->power -= transfer_amount;
    }
    return transfer_amount;
}
