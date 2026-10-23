/* check.c: what lux_step checks before it changes anything.
 *
 * Every state the Python engine reaches between two turns of a live game keeps
 * the invariants below, and the step relies on them: with them, nothing inside
 * the step can fail. So a step is either refused here, leaving the state
 * untouched, or runs to its end. Where the step relies on one, an assert names
 * the Python exception it rules out. */
#include "internal.h"

/* The units, per team: in id order (OrderedDict order is creation order, which
 * is id order), each id given out before global_id, a known type, on the map, and
 * a queue no longer than UNIT_ACTION_QUEUE_SIZE whose actions all passed the
 * action space check. Then at most one unit per tile, found by building the units
 * map. With one unit per tile, a team never has more than the map's tiles, so its
 * units and the one build per factory fit in LUX_MAX_UNITS_PER_TEAM. */
static int check_units(const lux_state *s, lux_workspace *ws)
{
    const lux_config *cfg = &s->env_cfg;
    for (int32_t team = 0; team < 2; team++) {
        if (s->n_units[team] < 0 || s->n_units[team] > LUX_MAX_TILES) {
            return LUX_ERR_STATE;
        }
        for (int32_t i = 0; i < s->n_units[team]; i++) {
            const lux_unit *u = &s->units[team][i];
            int64_t previous = i > 0 ? s->units[team][i - 1].unit_id : -1;
            if (u->team_id != team || (u->unit_type != LUX_LIGHT && u->unit_type != LUX_HEAVY) ||
                u->unit_id <= previous || u->unit_id >= s->global_id || !lux_in_bounds(s, u->x, u->y) ||
                u->queue_len < 0 || u->queue_len > cfg->UNIT_ACTION_QUEUE_SIZE) {
                return LUX_ERR_STATE;
            }
            for (int32_t k = 0; k < u->queue_len; k++) {
                if (!action_in_space(cfg, &u->action_queue[k])) {
                    return LUX_ERR_STATE;
                }
            }
        }
    }
    return board_build_units_map(s, ws);
}

/* The factories, per team: in id order (placement order), each id given out
 * before global_id, an empty queue (a turn's action is popped the same turn), and
 * factory_occupancy_map holding each one's id on exactly the tiles of its 3x3 and
 * -1 everywhere else. The map is what get_factory_at and the move check read. */
static int check_factories(const lux_state *s)
{
    const lux_board *b = &s->board;
    int64_t covered = 0;
    for (int32_t team = 0; team < 2; team++) {
        if (s->n_factories[team] < 0 || s->n_factories[team] > LUX_MAX_FACTORIES_PER_TEAM) {
            return LUX_ERR_STATE;
        }
        for (int32_t i = 0; i < s->n_factories[team]; i++) {
            const lux_factory *f = &s->factories[team][i];
            int64_t previous = i > 0 ? s->factories[team][i - 1].num_id : -1;
            if (f->team_id != team || f->num_id <= previous || f->num_id >= s->global_id ||
                f->queue_len != 0 || !lux_in_bounds(s, f->x, f->y)) {
                return LUX_ERR_STATE;
            }
            int64_t x_lo, x_hi, y_lo, y_hi;
            factory_pos_slice(s, f, &x_lo, &x_hi, &y_lo, &y_hi);
            for (int64_t x = x_lo; x < x_hi; x++) {
                for (int64_t y = y_lo; y < y_hi; y++) {
                    if (b->factory_occupancy_map[lux_tile(s, x, y)] != f->num_id) {
                        return LUX_ERR_STATE;
                    }
                }
            }
            covered += (x_hi - x_lo) * (y_hi - y_lo);
        }
    }
    /* No other tile names a factory. Two factories can't share a tile either: it
     * can't hold both ids. */
    const int64_t size = s->env_cfg.map_size;
    for (int64_t x = 0; x < size; x++) {
        for (int64_t y = 0; y < size; y++) {
            if (b->factory_occupancy_map[lux_tile(s, x, y)] != -1) {
                covered -= 1;
            }
        }
    }
    return covered == 0 ? LUX_OK : LUX_ERR_STATE;
}

int check_state(const lux_state *s, lux_workspace *ws)
{
    for (int32_t team = 0; team < 2; team++) {
        if (s->teams[team].n_factory_strains < 0 || s->teams[team].n_factory_strains > LUX_MAX_FACTORIES_PER_TEAM) {
            return LUX_ERR_STATE;
        }
    }
    int rc = check_factories(s);
    if (rc != LUX_OK) {
        return rc;
    }
    return check_units(s, ws);
}

/* The arguments: counts and pointers that hold together, known key kinds, and no
 * unit or factory named twice in one player's commands, which a dict can't do. */
int check_commands(const lux_state *s, lux_workspace *ws, const lux_agent_commands commands[2])
{
    for (int32_t team = 0; team < 2; team++) {
        const lux_agent_commands *cmds = &commands[team];
        if (cmds->n_commands < 0 || (cmds->n_commands > 0 && cmds->commands == NULL)) {
            return LUX_ERR_ARGUMENT;
        }
        for (int32_t i = 0; i < cmds->n_commands; i++) {
            const lux_command *c = &cmds->commands[i];
            if (c->kind == LUX_KEY_UNIT) {
                if (c->n_actions < 0 || (c->n_actions > 0 && c->actions == NULL)) {
                    return LUX_ERR_ARGUMENT;
                }
                int32_t u = lux_find_unit(s, team, c->id);
                if (u >= 0) {
                    if (ws->named_unit[team][u]) {
                        return LUX_ERR_ARGUMENT;
                    }
                    ws->named_unit[team][u] = 1;
                }
            } else if (c->kind == LUX_KEY_FACTORY) {
                int32_t f = lux_find_factory(s, team, c->id);
                if (f >= 0) {
                    if (ws->named_factory[team][f]) {
                        return LUX_ERR_ARGUMENT;
                    }
                    ws->named_factory[team][f] = 1;
                }
            } else if (c->kind != LUX_KEY_OTHER) {
                return LUX_ERR_ARGUMENT;
            }
        }
    }
    return LUX_OK;
}
