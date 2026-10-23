/* actions.c: actions.py, action formats and validate_actions. */
#include "internal.h"

/* a[1] = direction (0 = center, 1 = up, 2 = right, 3 = down, 4 = left) */
const int64_t lux_move_deltas[5][2] = {{0, 0}, {0, -1}, {1, 0}, {0, 1}, {-1, 0}};

/* format_action_vec: the action object built from a raw vector, written as its
 * state_dict(), which zeroes the fields its type doesn't keep. Returns -1 where
 * Python raises ValueError (an unknown action type). */
int format_action_vec(const lux_action *a, lux_action *act)
{
    lux_action out = {0};
    out.act_type = a->act_type;
    out.repeat = a->repeat;
    out.n = a->n;
    switch (a->act_type) {
    case LUX_MOVE: /* MoveAction(a[1], dist=1, repeat=a[4], n=a[5]) */
        out.direction = a->direction;
        break;
    case LUX_TRANSFER: /* TransferAction(a[1], a[2], a[3], repeat=a[4], n=a[5]) */
        out.direction = a->direction;
        out.resource = a->resource;
        out.amount = a->amount;
        break;
    case LUX_PICKUP: /* PickupAction(a[2], a[3], repeat=a[4], n=a[5]) */
        out.resource = a->resource;
        out.amount = a->amount;
        break;
    case LUX_DIG:           /* DigAction(repeat=a[4], n=a[5]) */
    case LUX_SELF_DESTRUCT: /* SelfDestructAction(repeat=a[4], n=a[5]) */
        break;
    case LUX_RECHARGE: /* RechargeAction(a[3], repeat=a[4], n=a[5]) */
        out.amount = a->amount;
        break;
    default:
        return -1;
    }
    *act = out;
    return 0;
}

/* format_factory_action: -1 where Python raises ValueError. */
int format_factory_action(int64_t a)
{
    return (a == LUX_BUILD_LIGHT || a == LUX_BUILD_HEAVY || a == LUX_WATER) ? 0 : -1;
}

/* The Box of get_act_space: low [0, 0, 0, 0, 0, 1],
 * high [5, 4, 4, max_transfer_amount + 1, 9999, 9999]. */
int action_in_space(const lux_config *cfg, const lux_action *a)
{
    return a->act_type >= 0 && a->act_type <= 5 && a->direction >= 0 && a->direction <= 4 &&
           a->resource >= 0 && a->resource <= 4 && a->amount >= 0 &&
           a->amount <= cfg->max_transfer_amount + 1 && a->repeat >= 0 && a->repeat <= 9999 &&
           a->n >= 1 && a->n <= 9999;
}

/* validate_actions: drops the actions the board makes impossible and sets the
 * power costs. Invalid actions are not evaluated and won't count towards the `n`
 * of actions that execute multiple times. Each list keeps its order.
 * LUX_ERR_STATE where Board.get_factory_at would raise KeyError. */
int validate_actions(lux_state *s, lux_workspace *ws)
{
    const lux_config *cfg = &s->env_cfg;
    const lux_board *b = &s->board;
    int err = LUX_OK;
    int32_t kept;

    lux_act *transfers = ws->acts[ACT_TRANSFER];
    kept = 0;
    for (int32_t i = 0; i < ws->n_acts[ACT_TRANSFER]; i++) {
        const lux_unit *unit = lux_unit_at(s, transfers[i].who);
        const lux_action *transfer_action = transfers[i].action;
        int64_t tx = unit->x + lux_move_deltas[transfer_action->direction][0];
        int64_t ty = unit->y + lux_move_deltas[transfer_action->direction][1];
        if (transfer_action->resource > 4 || transfer_action->resource < 0) {
            continue; /* transferring invalid resource id */
        }
        if (tx < 0 || ty < 0 || tx >= cfg->map_size || ty >= cfg->map_size) {
            continue; /* tried to transfer off the map */
        }
        transfers[kept++] = transfers[i];
    }
    ws->n_acts[ACT_TRANSFER] = kept;

    lux_act *digs = ws->acts[ACT_DIG];
    kept = 0;
    for (int32_t i = 0; i < ws->n_acts[ACT_DIG]; i++) {
        const lux_unit *unit = lux_unit_at(s, digs[i].who);
        int64_t dig_cost = cfg->ROBOTS[unit->unit_type].DIG_COST;
        if (dig_cost > unit->power) {
            continue;
        }
        /* verify not digging over a factory which is not allowed */
        if (b->factory_occupancy_map[lux_tile(s, unit->x, unit->y)] != -1) {
            continue;
        }
        digs[kept++] = digs[i];
    }
    ws->n_acts[ACT_DIG] = kept;

    lux_act *pickups = ws->acts[ACT_PICKUP];
    kept = 0;
    for (int32_t i = 0; i < ws->n_acts[ACT_PICKUP]; i++) {
        const lux_unit *unit = lux_unit_at(s, pickups[i].who);
        const lux_factory *factory = board_get_factory_at(s, unit->x, unit->y, &err);
        if (err != LUX_OK) {
            return err;
        }
        if (factory == NULL) {
            continue; /* no factory to pickup from */
        }
        pickups[kept++] = pickups[i];
    }
    ws->n_acts[ACT_PICKUP] = kept;

    lux_act *moves = ws->acts[ACT_MOVE];
    kept = 0;
    for (int32_t i = 0; i < ws->n_acts[ACT_MOVE]; i++) {
        const lux_unit *unit = lux_unit_at(s, moves[i].who);
        const lux_action *move_action = moves[i].action;
        /* dist is always 1 */
        int64_t tx = unit->x + 1 * lux_move_deltas[move_action->direction][0];
        int64_t ty = unit->y + 1 * lux_move_deltas[move_action->direction][1];
        if (tx < 0 || ty < 0 || tx >= cfg->map_size || ty >= cfg->map_size) {
            continue; /* tried to move off the map */
        }
        int32_t target = lux_tile(s, tx, ty);
        int64_t factory_id = b->factory_occupancy_map[target];
        if (factory_id != -1 && lux_find_factory(s, unit->team_id, factory_id) < 0) {
            continue; /* if there is a factory but not same team */
        }
        int64_t rubble = b->rubble[target];
        int64_t power_required =
            move_action->direction == 0 ? 0 : unit_move_power_cost(cfg, unit, rubble);
        if (power_required > unit->power) {
            continue;
        }
        moves[i].power_cost = power_required;
        moves[kept++] = moves[i];
    }
    ws->n_acts[ACT_MOVE] = kept;

    lux_act *self_destructs = ws->acts[ACT_SELF_DESTRUCT];
    kept = 0;
    for (int32_t i = 0; i < ws->n_acts[ACT_SELF_DESTRUCT]; i++) {
        const lux_unit *unit = lux_unit_at(s, self_destructs[i].who);
        int64_t power_required = cfg->ROBOTS[unit->unit_type].SELF_DESTRUCT_COST;
        if (power_required > unit->power) {
            continue;
        }
        self_destructs[i].power_cost = power_required;
        self_destructs[kept++] = self_destructs[i];
    }
    ws->n_acts[ACT_SELF_DESTRUCT] = kept;

    /* every recharge action is valid */

    lux_act *builds = ws->acts[ACT_FACTORY_BUILD];
    kept = 0;
    for (int32_t i = 0; i < ws->n_acts[ACT_FACTORY_BUILD]; i++) {
        const lux_factory *factory = &s->factories[builds[i].who.team][builds[i].who.index];
        const lux_unit_config *unit_cfg = &cfg->ROBOTS[builds[i].unit_type];
        if (factory->metal < unit_cfg->METAL_COST) {
            continue; /* insufficient metal */
        }
        int64_t power_required = unit_cfg->POWER_COST;
        if (factory->power < power_required) {
            continue; /* insufficient power */
        }
        builds[i].power_cost = power_required;
        builds[kept++] = builds[i];
    }
    ws->n_acts[ACT_FACTORY_BUILD] = kept;

    lux_act *waters = ws->acts[ACT_FACTORY_WATER];
    kept = 0;
    for (int32_t i = 0; i < ws->n_acts[ACT_FACTORY_WATER]; i++) {
        const lux_factory *factory = &s->factories[waters[i].who.team][waters[i].who.index];
        /* last turn's grow_lichen_positions: cache_water_info runs later in the step */
        int64_t water_cost = factory_water_cost(factory, cfg);
        if (water_cost > factory->water) {
            continue; /* insufficient water */
        }
        waters[kept++] = waters[i];
    }
    ws->n_acts[ACT_FACTORY_WATER] = kept;

    return LUX_OK;
}
