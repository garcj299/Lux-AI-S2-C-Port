/* env.c: env.py LuxAI_S2, the step of a main-game turn and the game logic it uses. */
#include <string.h>

#include "internal.h"

/* ---- Game logic ------------------------------------------------------------- */

/* LuxAI_S2.add_unit */
static int add_unit(lux_state *s, lux_workspace *ws, int32_t team, int32_t unit_type, int64_t x, int64_t y)
{
    if (s->n_units[team] >= LUX_MAX_UNITS_PER_TEAM) {
        return LUX_ERR_CAPACITY;
    }
    int32_t index = s->n_units[team];
    lux_unit *unit = &s->units[team][index];
    unit_init(unit, &s->env_cfg, team, unit_type, s->global_id);
    unit->x = x;
    unit->y = y;
    s->global_id += 1;
    s->n_units[team] += 1;
    ws->dead[team][index] = 0;

    int32_t tile = lux_tile(s, x, y);
    if (ws->umap_n[tile] >= LUX_UMAP_CAP) {
        return LUX_ERR_CAPACITY;
    }
    ws->umap[tile][ws->umap_n[tile]] = (lux_ref){team, index};
    ws->umap_n[tile] += 1;
    return LUX_OK;
}

/* LuxAI_S2.destroy_unit. This doesn't remove the unit from the units map. Python
 * deletes it from state.units at once; here it is marked dead and removed when the
 * step ends, because the handlers still holding its action keep running it. */
static void destroy_unit(lux_state *s, lux_workspace *ws, lux_ref r)
{
    const lux_unit *unit = lux_unit_at(s, r);
    lux_board *b = &s->board;
    int32_t tile = lux_tile(s, unit->x, unit->y);
    b->rubble[tile] = py_min(b->rubble[tile] + s->env_cfg.ROBOTS[unit->unit_type].RUBBLE_AFTER_DESTRUCTION,
                             s->env_cfg.MAX_RUBBLE);
    b->lichen[tile] = 0;
    b->lichen_strains[tile] = -1;
    ws->dead[r.team][r.index] = 1;
}

/* LuxAI_S2.destroy_factory, the board part: spray rubble on every factory tile and
 * clear its occupancy. The caller removes it from the team's factories. */
static void destroy_factory(lux_state *s, const lux_factory *factory)
{
    lux_board *b = &s->board;
    int64_t x_lo, x_hi, y_lo, y_hi;
    factory_pos_slice(s, factory, &x_lo, &x_hi, &y_lo, &y_hi);
    for (int64_t x = x_lo; x < x_hi; x++) {
        for (int64_t y = y_lo; y < y_hi; y++) {
            int32_t tile = lux_tile(s, x, y);
            b->rubble[tile] += s->env_cfg.FACTORY_RUBBLE_AFTER_DESTRUCTION;
            /* .clip(0, MAX_RUBBLE) */
            if (b->rubble[tile] < 0) {
                b->rubble[tile] = 0;
            } else if (b->rubble[tile] > s->env_cfg.MAX_RUBBLE) {
                b->rubble[tile] = s->env_cfg.MAX_RUBBLE;
            }
            b->factory_occupancy_map[tile] = -1;
        }
    }
}

/* ---- Units map helpers -------------------------------------------------------- */

/* list.remove(unit): drops the first entry naming it. LUX_ERR_STATE where Python
 * raises ValueError (not in the list). */
static int umap_remove(lux_workspace *ws, int32_t tile, lux_ref r)
{
    for (int32_t i = 0; i < ws->umap_n[tile]; i++) {
        if (ws->umap[tile][i].team == r.team && ws->umap[tile][i].index == r.index) {
            for (int32_t j = i + 1; j < ws->umap_n[tile]; j++) {
                ws->umap[tile][j - 1] = ws->umap[tile][j];
            }
            ws->umap_n[tile] -= 1;
            return LUX_OK;
        }
    }
    return LUX_ERR_STATE;
}

static void touch(lux_workspace *ws, int32_t tile)
{
    if (!ws->is_touched[tile]) {
        ws->is_touched[tile] = 1;
        ws->touched[ws->n_touched++] = tile;
    }
}

/* new_units_map[tile].append(unit) */
static int new_map_append(lux_workspace *ws, int32_t tile, lux_ref r)
{
    if (ws->new_map_n[tile] >= LUX_NEW_MAP_CAP) {
        return LUX_ERR_CAPACITY;
    }
    touch(ws, tile);
    ws->new_map[tile][ws->new_map_n[tile]++] = r;
    return LUX_OK;
}

/* all_destroyed_units.add(unit): a set, so each unit is destroyed once */
static void destroyed_add(lux_workspace *ws, lux_ref r)
{
    if (!ws->in_destroyed[r.team][r.index]) {
        ws->in_destroyed[r.team][r.index] = 1;
        ws->destroyed[ws->n_destroyed++] = r;
    }
}

/* ---- Action handlers, in the order step() runs them -------------------------- */

/* LuxAI_S2._handle_dig_actions */
static void handle_dig_actions(lux_state *s, lux_workspace *ws)
{
    const lux_config *cfg = &s->env_cfg;
    lux_board *b = &s->board;
    for (int32_t i = 0; i < ws->n_acts[ACT_DIG]; i++) {
        lux_unit *unit = lux_unit_at(s, ws->acts[ACT_DIG][i].who);
        lux_action *dig_action = ws->acts[ACT_DIG][i].action;
        const lux_unit_config *ucfg = &cfg->ROBOTS[unit->unit_type];
        int32_t tile = lux_tile(s, unit->x, unit->y);
        if (b->rubble[tile] > 0) {
            b->rubble[tile] = py_max(b->rubble[tile] - ucfg->DIG_RUBBLE_REMOVED, 0);
        } else if (b->lichen[tile] > 0) {
            int64_t lichen_left = py_max(b->lichen[tile] - ucfg->DIG_LICHEN_REMOVED, 0);
            b->lichen[tile] = lichen_left;
            if (lichen_left == 0) { /* dug out the last lichen */
                /* sic: DIG_RESOURCE_GAIN, not a rubble constant */
                b->rubble[tile] = ucfg->DIG_RESOURCE_GAIN;
            }
        } else if (b->ice[tile] > 0) {
            unit_add_resource(cfg, unit, 0, ucfg->DIG_RESOURCE_GAIN);
        } else if (b->ore[tile] > 0) {
            unit_add_resource(cfg, unit, 1, ucfg->DIG_RESOURCE_GAIN);
        }
        unit->power -= ucfg->DIG_COST;
        unit_repeat_action(unit, dig_action);
    }
}

/* LuxAI_S2._handle_self_destruct_actions */
static int handle_self_destruct_actions(lux_state *s, lux_workspace *ws)
{
    for (int32_t i = 0; i < ws->n_acts[ACT_SELF_DESTRUCT]; i++) {
        lux_ref who = ws->acts[ACT_SELF_DESTRUCT][i].who;
        const lux_unit *unit = lux_unit_at(s, who);
        int32_t tile = lux_tile(s, unit->x, unit->y);
        /* del self.state.board.units_map[pos_hash]: the whole entry */
        if (ws->umap_n[tile] == 0) {
            return LUX_ERR_STATE; /* KeyError */
        }
        ws->umap_n[tile] = 0;
        destroy_unit(s, ws, who);
    }
    return LUX_OK;
}

/* LuxAI_S2._handle_factory_build_actions. Validation already checked metal and
 * power; Python checks them again here, and so does this. */
static int handle_factory_build_actions(lux_state *s, lux_workspace *ws)
{
    const lux_config *cfg = &s->env_cfg;
    for (int32_t i = 0; i < ws->n_acts[ACT_FACTORY_BUILD]; i++) {
        const lux_act *act = &ws->acts[ACT_FACTORY_BUILD][i];
        lux_factory *factory = &s->factories[act->who.team][act->who.index];
        if (act->unit_type == LUX_HEAVY) {
            if (factory->metal < cfg->ROBOTS[LUX_HEAVY].METAL_COST) {
                continue;
            }
            if (factory->power < act->power_cost) {
                continue;
            }
            factory_sub_resource(factory, 3, cfg->ROBOTS[LUX_HEAVY].METAL_COST);
            factory_sub_resource(factory, 4, act->power_cost);
        } else {
            if (factory->metal < cfg->ROBOTS[LUX_LIGHT].METAL_COST) {
                continue;
            }
            if (factory->power < act->power_cost) {
                continue;
            }
            factory_sub_resource(factory, 3, cfg->ROBOTS[LUX_LIGHT].METAL_COST);
            factory_sub_resource(factory, 4, act->power_cost);
        }
        int rc = add_unit(s, ws, act->who.team, act->unit_type, factory->x, factory->y);
        if (rc != LUX_OK) {
            return rc;
        }
    }
    return LUX_OK;
}

/* LuxAI_S2._handle_movement_actions.
 *
 * new_units_map lists, for each tile, the units entering it in move order, then
 * the units staying on it in units_map order. Each tile's collision depends only
 * on its own list, and destroying units commutes (rubble is capped with min, lichen
 * and strain are set), so the order the tiles are visited in doesn't matter: only
 * the order inside each list does. */
static int move_units(lux_state *s, lux_workspace *ws)
{
    const lux_config *cfg = &s->env_cfg;
    int rc;

    for (int32_t i = 0; i < ws->n_acts[ACT_MOVE]; i++) {
        lux_ref who = ws->acts[ACT_MOVE][i].who;
        lux_unit *unit = lux_unit_at(s, who);
        lux_action *move_action = ws->acts[ACT_MOVE][i].action;
        /* skip move center */
        if (move_action->direction != 0) {
            int32_t old_tile = lux_tile(s, unit->x, unit->y);
            int64_t tx = unit->x + 1 * lux_move_deltas[move_action->direction][0];
            int64_t ty = unit->y + 1 * lux_move_deltas[move_action->direction][1];
            int64_t power_required = ws->acts[ACT_MOVE][i].power_cost;
            unit->x = tx;
            unit->y = ty;
            int32_t new_tile = lux_tile(s, tx, ty);

            /* remove unit from map temporarily */
            if (ws->umap_n[old_tile] == 1) {
                ws->umap_n[old_tile] = 0;
            } else {
                rc = umap_remove(ws, old_tile, who);
                if (rc != LUX_OK) {
                    return rc;
                }
            }

            rc = new_map_append(ws, new_tile, who);
            if (rc != LUX_OK) {
                return rc;
            }
            unit->power -= power_required;

            if (unit->unit_type == LUX_HEAVY) {
                if (ws->heavy_entered_n[new_tile] >= LUX_ENTER_CAP) {
                    return LUX_ERR_CAPACITY;
                }
                ws->heavy_entered[new_tile][ws->heavy_entered_n[new_tile]++] = who;
            } else {
                if (ws->light_entered_n[new_tile] >= LUX_ENTER_CAP) {
                    return LUX_ERR_CAPACITY;
                }
                ws->light_entered[new_tile][ws->light_entered_n[new_tile]++] = who;
            }
        }
        unit_repeat_action(unit, move_action);
    }

    /* add in all the stationary units: every tile still in units_map has its units
     * standing on it, so visiting each living unit's tile reaches all of them */
    for (int32_t team = 0; team < 2; team++) {
        for (int32_t index = 0; index < s->n_units[team]; index++) {
            if (ws->dead[team][index]) {
                continue;
            }
            const lux_unit *unit = &s->units[team][index];
            int32_t tile = lux_tile(s, unit->x, unit->y);
            if (ws->merged[tile] || ws->umap_n[tile] == 0) {
                continue;
            }
            ws->merged[tile] = 1;
            for (int32_t k = 0; k < ws->umap_n[tile]; k++) {
                rc = new_map_append(ws, tile, ws->umap[tile][k]);
                if (rc != LUX_OK) {
                    return rc;
                }
            }
        }
    }

    for (int32_t k = 0; k < ws->n_touched; k++) {
        int32_t tile = ws->touched[k];
        const lux_ref *units = ws->new_map[tile];
        int32_t n = ws->new_map_n[tile];
        ws->has_survivor[tile] = 0;
        if (n <= 1) {
            if (n == 1) {
                ws->survivor[tile] = units[0];
                ws->has_survivor[tile] = 1;
            }
            continue;
        }
        if (ws->heavy_entered_n[tile] > 1) {
            /* all units collide, find the top 2 units by power */
            lux_ref most, next;
            get_top_two_power_units(s, units, n, LUX_HEAVY, &most, &next);
            lux_unit *most_power_unit = lux_unit_at(s, most);
            const lux_unit *next_most_power_unit = lux_unit_at(s, next);
            if (most_power_unit->power == next_most_power_unit->power) {
                /* tie, all units break */
                for (int32_t j = 0; j < n; j++) {
                    destroyed_add(ws, units[j]);
                }
            } else {
                int64_t most_power_unit_power_loss =
                    py_ceil((double)next_most_power_unit->power * cfg->POWER_LOSS_FACTOR);
                most_power_unit->power -= most_power_unit_power_loss;
                for (int32_t j = 0; j < n; j++) {
                    if (lux_unit_at(s, units[j])->unit_id != most_power_unit->unit_id) {
                        destroyed_add(ws, units[j]);
                    }
                }
                ws->survivor[tile] = most;
                ws->has_survivor[tile] = 1;
            }
        } else if (ws->heavy_entered_n[tile] > 0) {
            /* all other units collide and break */
            lux_ref surviving = ws->heavy_entered[tile][0];
            int64_t surviving_id = lux_unit_at(s, surviving)->unit_id;
            for (int32_t j = 0; j < n; j++) {
                if (lux_unit_at(s, units[j])->unit_id != surviving_id) {
                    destroyed_add(ws, units[j]);
                }
            }
            ws->survivor[tile] = surviving;
            ws->has_survivor[tile] = 1;
        } else {
            /* check for stationary heavy unit there */
            int has_surviving = 0;
            lux_ref surviving = {0, 0};
            int has_heavy_stationary = 0;
            lux_ref heavy_stationary = {0, 0};
            for (int32_t j = 0; j < n; j++) {
                if (lux_unit_at(s, units[j])->unit_type == LUX_HEAVY) {
                    if (has_heavy_stationary) {
                        /* >= 2 heavies stationary in a tile where no heavies are entering
                         * (a factory built one under another): none counts as the
                         * stationary heavy */
                        has_heavy_stationary = 0;
                        break;
                    }
                    has_heavy_stationary = 1;
                    heavy_stationary = units[j];
                }
            }

            if (has_heavy_stationary) {
                has_surviving = 1;
                surviving = heavy_stationary;
            } else {
                if (ws->light_entered_n[tile] > 1) {
                    /* all units collide, get top 2 units by power */
                    lux_ref most, next;
                    get_top_two_power_units(s, units, n, LUX_LIGHT, &most, &next);
                    lux_unit *most_power_unit = lux_unit_at(s, most);
                    const lux_unit *next_most_power_unit = lux_unit_at(s, next);
                    if (most_power_unit->power == next_most_power_unit->power) {
                        /* tie, all units break */
                        for (int32_t j = 0; j < n; j++) {
                            destroyed_add(ws, units[j]);
                        }
                    } else {
                        int64_t most_power_unit_power_loss =
                            py_ceil((double)next_most_power_unit->power * cfg->POWER_LOSS_FACTOR);
                        most_power_unit->power -= most_power_unit_power_loss;
                        has_surviving = 1;
                        surviving = most;
                    }
                } else if (ws->light_entered_n[tile] > 0) {
                    /* light crashes into stationary light unit */
                    has_surviving = 1;
                    surviving = ws->light_entered[tile][0];
                }
            }
            if (!has_surviving) {
                for (int32_t j = 0; j < n; j++) {
                    destroyed_add(ws, units[j]);
                }
            } else {
                int64_t surviving_id = lux_unit_at(s, surviving)->unit_id;
                for (int32_t j = 0; j < n; j++) {
                    if (lux_unit_at(s, units[j])->unit_id != surviving_id) {
                        destroyed_add(ws, units[j]);
                    }
                }
                ws->survivor[tile] = surviving;
                ws->has_survivor[tile] = 1;
            }
        }
    }

    /* self.state.board.units_map = new_units_map_after_collision. Every tile left in
     * units_map was touched above, so this replaces it whole. */
    for (int32_t k = 0; k < ws->n_touched; k++) {
        int32_t tile = ws->touched[k];
        if (ws->has_survivor[tile]) {
            ws->umap[tile][0] = ws->survivor[tile];
            ws->umap_n[tile] = 1;
        } else {
            ws->umap_n[tile] = 0;
        }
    }

    for (int32_t k = 0; k < ws->n_destroyed; k++) {
        destroy_unit(s, ws, ws->destroyed[k]);
    }
    return LUX_OK;
}

/* Runs move_units, then clears its scratch. */
static int handle_movement_actions(lux_state *s, lux_workspace *ws)
{
    ws->n_touched = 0;
    ws->n_destroyed = 0;
    int rc = move_units(s, ws);

    /* clear the scratch, also after an error, as the workspace outlives the state */
    for (int32_t k = 0; k < ws->n_touched; k++) {
        int32_t tile = ws->touched[k];
        ws->new_map_n[tile] = 0;
        ws->heavy_entered_n[tile] = 0;
        ws->light_entered_n[tile] = 0;
        ws->merged[tile] = 0;
        ws->is_touched[tile] = 0;
    }
    for (int32_t k = 0; k < ws->n_destroyed; k++) {
        ws->in_destroyed[ws->destroyed[k].team][ws->destroyed[k].index] = 0;
    }
    ws->n_touched = 0;
    ws->n_destroyed = 0;
    return rc;
}

/* LuxAI_S2._handle_recharge_actions: the action stays at the front, unspent, until
 * the unit has the power it waits for. */
static void handle_recharge_actions(lux_state *s, lux_workspace *ws)
{
    for (int32_t i = 0; i < ws->n_acts[ACT_RECHARGE]; i++) {
        lux_unit *unit = lux_unit_at(s, ws->acts[ACT_RECHARGE][i].who);
        lux_action *recharge_action = ws->acts[ACT_RECHARGE][i].action;
        if (unit->power < recharge_action->amount) {
            /* pass */
        } else {
            unit_repeat_action(unit, recharge_action);
        }
    }
}

/* LuxAI_S2._handle_factory_water_actions. grow_lichen_positions now holds what
 * cache_water_info found this turn, all factories' sets taken before any watering. */
static void handle_factory_water_actions(lux_state *s, lux_workspace *ws)
{
    const lux_config *cfg = &s->env_cfg;
    lux_board *b = &s->board;
    for (int32_t i = 0; i < ws->n_acts[ACT_FACTORY_WATER]; i++) {
        lux_ref who = ws->acts[ACT_FACTORY_WATER][i].who;
        lux_factory *factory = &s->factories[who.team][who.index];
        int64_t water_cost = factory_water_cost(factory, cfg);
        if (water_cost > factory->water) {
            continue;
        }
        factory->water -= water_cost;
        const int32_t *grow_tiles = ws->grow_tiles[who.team][who.index];
        for (int64_t k = 0; k < factory->grow_lichen_count; k++) {
            b->lichen[grow_tiles[k]] += 2;
            b->lichen_strains[grow_tiles[k]] = factory->num_id;
        }
    }
}

/* LuxAI_S2._handle_transfer_actions. It is important to first sub resource from
 * all units, and then add resource to targets. A unit destroyed in a collision
 * this turn still transfers here: its action was collected before it died. */
static int handle_transfer_actions(lux_state *s, lux_workspace *ws)
{
    const lux_config *cfg = &s->env_cfg;
    const lux_act *transfers = ws->acts[ACT_TRANSFER];
    int32_t n = ws->n_acts[ACT_TRANSFER];

    /* sub from unit cargo */
    for (int32_t i = 0; i < n; i++) {
        lux_unit *unit = lux_unit_at(s, transfers[i].who);
        const lux_action *transfer_action = transfers[i].action;
        ws->amount_list[i] = unit_sub_resource(unit, transfer_action->resource, transfer_action->amount);
    }

    /* add to target cargo */
    for (int32_t i = 0; i < n; i++) {
        lux_unit *unit = lux_unit_at(s, transfers[i].who);
        lux_action *transfer_action = transfers[i].action;
        int64_t transfer_amount = ws->amount_list[i];
        int64_t tx = unit->x + lux_move_deltas[transfer_action->direction][0];
        int64_t ty = unit->y + lux_move_deltas[transfer_action->direction][1];
        int32_t tile = lux_tile(s, tx, ty);
        /* if there is a factory, we prefer transferring to that entity */
        int32_t f = lux_find_factory(s, unit->team_id, s->board.factory_occupancy_map[tile]);
        if (f >= 0) {
            factory_add_resource(&s->factories[unit->team_id][f], transfer_action->resource, transfer_amount);
        } else if (ws->umap_n[tile] > 0) { /* units_there is not None */
            if (ws->umap_n[tile] != 1) {
                return LUX_ERR_STATE; /* assert len(units_there) == 1 */
            }
            lux_unit *target_unit = lux_unit_at(s, ws->umap[tile][0]);
            /* add resources to target. This will waste (transfer_amount - actually_transferred) resources */
            unit_add_resource(cfg, target_unit, transfer_action->resource, transfer_amount);
        }
        unit_repeat_action(unit, transfer_action);
    }
    return LUX_OK;
}

/* LuxAI_S2._handle_pickup_actions. As with transfers, a unit destroyed in a
 * collision this turn still picks up, and what it picks up is lost. */
static int handle_pickup_actions(lux_state *s, lux_workspace *ws)
{
    const lux_config *cfg = &s->env_cfg;
    for (int32_t i = 0; i < ws->n_acts[ACT_PICKUP]; i++) {
        lux_unit *unit = lux_unit_at(s, ws->acts[ACT_PICKUP][i].who);
        lux_action *pickup_action = ws->acts[ACT_PICKUP][i].action;
        int err = LUX_OK;
        lux_factory *factory = board_get_factory_at(s, unit->x, unit->y, &err);
        if (err != LUX_OK) {
            return err;
        }
        if (factory == NULL) {
            return LUX_ERR_STATE; /* None.sub_resource raises AttributeError */
        }
        int64_t pickup_amount = factory_sub_resource(factory, pickup_action->resource, pickup_action->amount);
        /* may waste resources if tried to pickup more than one can hold. */
        unit_add_resource(cfg, unit, pickup_action->resource, pickup_amount);
        unit_repeat_action(unit, pickup_action);
    }
    return LUX_OK;
}

/* ---- The start of step() --------------------------------------------------------- */

/* The queue updates of step(), for one player. Python runs them for every player
 * in the actions dict, failed or not, inside a try: the first exception fails the
 * player and skips the rest of its entries. */
static int update_action_queues(lux_state *s, int32_t team, const lux_agent_commands *cmds, int32_t *failed)
{
    const lux_config *cfg = &s->env_cfg;
    for (int32_t i = 0; i < cmds->n_commands; i++) {
        const lux_command *c = &cmds->commands[i];
        if (c->kind == LUX_KEY_FACTORY) {
            int32_t f = c->id >= 0 ? lux_find_factory(s, team, c->id) : -1;
            if (f < 0) {
                *failed = 1; /* KeyError */
                break;
            }
            if (format_factory_action(c->factory_action) != 0) {
                *failed = 1; /* ValueError */
                break;
            }
            lux_factory *factory = &s->factories[team][f];
            if (factory->queue_len >= LUX_FACTORY_QUEUE_CAP) {
                return LUX_ERR_CAPACITY;
            }
            factory->action_queue[factory->queue_len++] = c->factory_action;
        } else if (c->kind == LUX_KEY_UNIT) {
            int32_t u = c->id >= 0 ? lux_find_unit(s, team, c->id) : -1;
            if (u < 0) {
                *failed = 1; /* KeyError */
                break;
            }
            lux_unit *unit = &s->units[team][u];
            /* if unit does not have more than ACTION_QUEUE_POWER_COST power, we skip
             * updating the action queue */
            int64_t update_power_req = cfg->ROBOTS[unit->unit_type].ACTION_QUEUE_POWER_COST;
            if (unit->power < update_power_req) {
                continue;
            }
            int32_t n = c->n_actions;
            if (n > cfg->UNIT_ACTION_QUEUE_SIZE) {
                n = (int32_t)cfg->UNIT_ACTION_QUEUE_SIZE; /* action[:UNIT_ACTION_QUEUE_SIZE] */
            }
            lux_action formatted_actions[LUX_MAX_QUEUE];
            int bad = 0;
            for (int32_t k = 0; k < n; k++) {
                if (format_action_vec(&c->actions[k], &formatted_actions[k]) != 0) {
                    bad = 1;
                    break;
                }
            }
            if (bad) {
                *failed = 1; /* ValueError: nothing changes for this unit */
                break;
            }
            unit->power -= update_power_req;
            memcpy(unit->action_queue, formatted_actions, (size_t)n * sizeof(formatted_actions[0]));
            unit->queue_len = n;
        }
        /* any other key: neither branch runs */
    }
    return LUX_OK;
}

/* Checks the arguments; also that no unit or factory is named twice, which a dict
 * can't do. */
static int check_commands(const lux_state *s, lux_workspace *ws, const lux_agent_commands commands[2])
{
    int rc = LUX_OK;
    for (int32_t team = 0; team < 2 && rc == LUX_OK; team++) {
        const lux_agent_commands *cmds = &commands[team];
        if (cmds->n_commands < 0 || (cmds->n_commands > 0 && cmds->commands == NULL)) {
            rc = LUX_ERR_ARGUMENT;
            break;
        }
        for (int32_t i = 0; i < cmds->n_commands; i++) {
            const lux_command *c = &cmds->commands[i];
            if (c->kind == LUX_KEY_UNIT) {
                if (c->n_actions < 0 || (c->n_actions > 0 && c->actions == NULL)) {
                    rc = LUX_ERR_ARGUMENT;
                    break;
                }
                int32_t u = c->id >= 0 ? lux_find_unit(s, team, c->id) : -1;
                if (u >= 0) {
                    if (ws->named_unit[team][u]) {
                        rc = LUX_ERR_ARGUMENT;
                        break;
                    }
                    ws->named_unit[team][u] = 1;
                }
            } else if (c->kind == LUX_KEY_FACTORY) {
                int32_t f = c->id >= 0 ? lux_find_factory(s, team, c->id) : -1;
                if (f >= 0) {
                    if (ws->named_factory[team][f]) {
                        rc = LUX_ERR_ARGUMENT;
                        break;
                    }
                    ws->named_factory[team][f] = 1;
                }
            } else if (c->kind != LUX_KEY_OTHER) {
                rc = LUX_ERR_ARGUMENT;
                break;
            }
        }
    }
    /* clear the marks */
    for (int32_t team = 0; team < 2; team++) {
        memset(ws->named_unit[team], 0, (size_t)s->n_units[team] * sizeof(ws->named_unit[team][0]));
        memset(ws->named_factory[team], 0, sizeof(ws->named_factory[team]));
    }
    return rc;
}

/* What the rest of the step relies on about the factories and teams. */
static int check_factories_and_teams(const lux_state *s)
{
    for (int32_t team = 0; team < 2; team++) {
        if (s->n_factories[team] < 0 || s->n_factories[team] > LUX_MAX_FACTORIES_PER_TEAM) {
            return LUX_ERR_STATE;
        }
        for (int32_t i = 0; i < s->n_factories[team]; i++) {
            const lux_factory *f = &s->factories[team][i];
            if (f->team_id != team || f->queue_len < 0 || f->queue_len > LUX_FACTORY_QUEUE_CAP ||
                !lux_in_bounds(s, f->x, f->y)) {
                return LUX_ERR_STATE;
            }
            /* OrderedDict order is placement order, which is num_id order. It decides
             * which factory's new unit gets the lower id. */
            if (i > 0 && s->factories[team][i - 1].num_id >= f->num_id) {
                return LUX_ERR_STATE;
            }
        }
        if (s->teams[team].n_factory_strains < 0 ||
            s->teams[team].n_factory_strains > LUX_MAX_FACTORIES_PER_TEAM) {
            return LUX_ERR_STATE;
        }
    }
    return LUX_OK;
}

/* ---- step() ---------------------------------------------------------------- */

int lux_step(lux_state *s, lux_workspace *ws, const lux_agent_commands commands[2], lux_step_result *result)
{
    if (s == NULL || ws == NULL || commands == NULL || result == NULL) {
        return LUX_ERR_ARGUMENT;
    }
    int rc = lux_config_check(&s->env_cfg);
    if (rc != LUX_OK) {
        return rc;
    }
    const lux_config *cfg = &s->env_cfg;
    lux_board *b = &s->board;
    const int64_t size = cfg->map_size;

    /* After the last turn Python empties env.agents, and the next step raises. */
    if (s->done) {
        return LUX_ERR_GAME_OVER;
    }
    /* Turn 1 logic: the early game is the bid and the factory placement */
    if (s->env_steps == 0 || (cfg->BIDDING_SYSTEM && lux_real_env_steps(s) < 0)) {
        return LUX_ERR_EARLY_GAME;
    }
    rc = board_build_units_map(s, ws);
    if (rc != LUX_OK) {
        return rc;
    }
    rc = check_factories_and_teams(s);
    if (rc != LUX_OK) {
        return rc;
    }
    rc = check_commands(s, ws, commands);
    if (rc != LUX_OK) {
        return rc;
    }
    for (int32_t team = 0; team < 2; team++) {
        memset(ws->dead[team], 0, (size_t)s->n_units[team] * sizeof(ws->dead[team][0]));
    }
    for (int32_t a = 0; a < ACT_N; a++) {
        ws->n_acts[a] = 0;
    }

    int32_t failed_agents[2] = {0, 0};

    /* 1. Check for malformed actions */
    if (cfg->validate_action_space) {
        for (int32_t team = 0; team < 2; team++) {
            if (!act_space_contains(s, team, &commands[team])) {
                failed_agents[team] = 1;
            }
        }
    }

    for (int32_t team = 0; team < 2; team++) {
        rc = update_action_queues(s, team, &commands[team], &failed_agents[team]);
        if (rc != LUX_OK) {
            return rc;
        }
    }

    /* 2. store actions by type */
    for (int32_t team = 0; team < 2; team++) {
        if (failed_agents[team]) { /* skip failed agents */
            continue;
        }
        for (int32_t i = 0; i < s->n_units[team]; i++) {
            lux_unit *unit = &s->units[team][i];
            if (unit->queue_len == 0) {
                continue;
            }
            lux_action *unit_a = &unit->action_queue[0];
            if (!action_in_space(cfg, unit_a)) {
                /* only a failed player's queue can hold one, and its game is over */
                return LUX_ERR_STATE;
            }
            lux_act *act = &ws->acts[unit_a->act_type][ws->n_acts[unit_a->act_type]++];
            act->who = (lux_ref){team, i};
            act->action = unit_a;
            act->unit_type = 0;
            act->power_cost = 0;
        }
        for (int32_t i = 0; i < s->n_factories[team]; i++) {
            lux_factory *factory = &s->factories[team][i];
            if (factory->queue_len > 0) {
                int64_t code = factory->action_queue[0];
                memmove(&factory->action_queue[0], &factory->action_queue[1],
                        (size_t)(factory->queue_len - 1) * sizeof(factory->action_queue[0]));
                factory->queue_len -= 1;
                int32_t list;
                if (code == LUX_BUILD_LIGHT || code == LUX_BUILD_HEAVY) {
                    list = ACT_FACTORY_BUILD;
                } else if (code == LUX_WATER) {
                    list = ACT_FACTORY_WATER;
                } else {
                    return LUX_ERR_STATE;
                }
                lux_act *act = &ws->acts[list][ws->n_acts[list]++];
                act->who = (lux_ref){team, i};
                act->action = NULL;
                act->unit_type = code == LUX_BUILD_HEAVY ? LUX_HEAVY : LUX_LIGHT;
                act->power_cost = 0;
            }
        }
    }

    /* 3. validate all actions against current state, throw away impossible actions */
    rc = validate_actions(s, ws);
    if (rc != LUX_OK) {
        return rc;
    }

    handle_dig_actions(s, ws);
    rc = handle_self_destruct_actions(s, ws);
    if (rc != LUX_OK) {
        return rc;
    }
    rc = handle_factory_build_actions(s, ws);
    if (rc != LUX_OK) {
        return rc;
    }
    rc = handle_movement_actions(s, ws);
    if (rc != LUX_OK) {
        return rc;
    }
    handle_recharge_actions(s, ws);

    for (int32_t team = 0; team < 2; team++) {
        for (int32_t i = 0; i < s->n_factories[team]; i++) {
            /* update information for lichen growing and cache it */
            factory_cache_water_info(s, &s->factories[team][i], ws, ws->grow_tiles[team][i]);
        }
    }

    handle_factory_water_actions(s, ws);
    rc = handle_transfer_actions(s, ws);
    if (rc != LUX_OK) {
        return rc;
    }
    rc = handle_pickup_actions(s, ws);
    if (rc != LUX_OK) {
        return rc;
    }

    /* Update lichen */
    for (int64_t x = 0; x < size; x++) {
        for (int64_t y = 0; y < size; y++) {
            int32_t tile = lux_tile(s, x, y);
            b->lichen[tile] -= 1;
            if (b->lichen[tile] < 0) {
                b->lichen[tile] = 0;
            } else if (b->lichen[tile] > cfg->MAX_LICHEN_PER_TILE) {
                b->lichen[tile] = cfg->MAX_LICHEN_PER_TILE;
            }
            if (b->lichen[tile] == 0) {
                b->lichen_strains[tile] = -1;
            }
        }
    }

    /* resources refining */
    for (int32_t team = 0; team < 2; team++) {
        int32_t n = s->n_factories[team];
        for (int32_t i = 0; i < n; i++) {
            lux_factory *factory = &s->factories[team][i];
            factory_refine_step(factory, cfg);
            factory->water -= cfg->FACTORY_WATER_CONSUMPTION;
            ws->factory_destroy[i] = factory->water < 0;
        }
        /* destroy factories that ran out of water */
        int32_t kept = 0;
        for (int32_t i = 0; i < n; i++) {
            if (ws->factory_destroy[i]) {
                destroy_factory(s, &s->factories[team][i]);
            } else {
                s->factories[team][kept++] = s->factories[team][i];
            }
        }
        s->n_factories[team] = kept;
    }

    /* power gain */
    if (is_day(cfg, lux_real_env_steps(s))) {
        for (int32_t team = 0; team < 2; team++) {
            for (int32_t i = 0; i < s->n_units[team]; i++) {
                if (ws->dead[team][i]) {
                    continue;
                }
                lux_unit *u = &s->units[team][i];
                u->power = u->power + cfg->ROBOTS[u->unit_type].CHARGE;
                u->power = py_min(u->power, cfg->ROBOTS[u->unit_type].BATTERY_CAPACITY);
            }
        }
    }
    for (int32_t team = 0; team < 2; team++) {
        for (int32_t i = 0; i < s->n_factories[team]; i++) {
            lux_factory *f = &s->factories[team][i];
            /* natural nuclear energy generation */
            f->power = f->power + cfg->FACTORY_CHARGE;
            /* lichen/plant power */
            f->power = f->power + f->connected_lichen_count * cfg->POWER_PER_CONNECTED_LICHEN_TILE;
        }
    }

    /* always set rubble under factories to 0. */
    for (int64_t x = 0; x < size; x++) {
        for (int64_t y = 0; y < size; y++) {
            int32_t tile = lux_tile(s, x, y);
            if (b->factory_occupancy_map[tile] != -1) {
                b->rubble[tile] = 0;
            }
        }
    }

    /* rewards for all agents */
    int64_t real_env_steps = lux_real_env_steps(s);
    for (int32_t team = 0; team < 2; team++) {
        const lux_team *t = &s->teams[team];
        if (s->n_factories[team] == 0 && real_env_steps >= 0) {
            failed_agents[team] = 1; /* lost all factories */
        }
        if (failed_agents[team]) {
            result->rewards[team] = -1000;
        } else {
            int64_t lichen = 0;
            for (int64_t x = 0; x < size; x++) {
                for (int64_t y = 0; y < size; y++) {
                    int32_t tile = lux_tile(s, x, y);
                    for (int32_t k = 0; k < t->n_factory_strains; k++) {
                        if (b->lichen_strains[tile] == t->factory_strains[k]) {
                            lichen += b->lichen[tile];
                            break;
                        }
                    }
                }
            }
            result->rewards[team] = lichen;
        }
    }

    s->env_steps += 1;
    int env_done = lux_real_env_steps(s) >= cfg->max_episode_length;
    env_done = env_done || failed_agents[0] || failed_agents[1]; /* env is done if any agent fails. */
    if (env_done) {
        s->done = 1;
    }
    result->failed[0] = failed_agents[0];
    result->failed[1] = failed_agents[1];
    result->done = env_done;

    /* drop the units destroyed this turn, keeping the others' order */
    for (int32_t team = 0; team < 2; team++) {
        int32_t kept = 0;
        for (int32_t i = 0; i < s->n_units[team]; i++) {
            if (ws->dead[team][i]) {
                continue;
            }
            if (kept != i) {
                s->units[team][kept] = s->units[team][i];
            }
            kept++;
        }
        s->n_units[team] = kept;
    }
    return LUX_OK;
}
