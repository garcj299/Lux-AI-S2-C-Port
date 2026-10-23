/* unit.c: unit.py Unit. */
#include <string.h>

#include "internal.h"

/* Unit.__init__: no cargo, an empty queue, INIT_POWER. The caller sets pos. */
void unit_init(lux_unit *u, const lux_config *cfg, int32_t team_id, int32_t unit_type, int64_t unit_id)
{
    memset(u, 0, sizeof(*u));
    u->unit_id = unit_id;
    u->team_id = team_id;
    u->unit_type = unit_type;
    u->power = cfg->ROBOTS[unit_type].INIT_POWER;
}

/* Unit.repeat_action. `action` is the unit's queue front, as in every caller. */
void unit_repeat_action(lux_unit *u, lux_action *action)
{
    action->n -= 1;
    if (action->n <= 0) {
        lux_action popped = *action;
        /* remove from front of queue */
        memmove(&u->action_queue[0], &u->action_queue[1],
                (size_t)(u->queue_len - 1) * sizeof(u->action_queue[0]));
        u->queue_len -= 1;
        /* endless repeat puts action back at end of queue */
        if (popped.repeat > 0) {
            popped.n = popped.repeat;
            u->action_queue[u->queue_len] = popped;
            u->queue_len += 1;
        }
    }
}

/* Unit.move_power_cost: math.floor(MOVE_COST + RUBBLE_MOVEMENT_COST * rubble).
 * In Python the light unit's sum is a float64 (0.05 * rubble), the heavy unit's an
 * integer (RUBBLE_MOVEMENT_COST is the int 1); doing both in double gives the same
 * results, since the heavy unit's terms are small integers, which doubles hold exactly. */
int64_t unit_move_power_cost(const lux_config *cfg, const lux_unit *u, int64_t rubble_at_target)
{
    const lux_unit_config *ucfg = &cfg->ROBOTS[u->unit_type];
    return py_floor((double)ucfg->MOVE_COST + ucfg->RUBBLE_MOVEMENT_COST * (double)rubble_at_target);
}

/* Unit.add_resource: adds up to the free cargo space or battery. Returns how much
 * was added. resource_id is 0-4 (the queue's actions are in the action space). */
int64_t unit_add_resource(const lux_config *cfg, lux_unit *u, int64_t resource_id, int64_t amount)
{
    const lux_unit_config *ucfg = &cfg->ROBOTS[u->unit_type];
    int64_t transfer_amount = 0;
    if (amount < 0) {
        amount = 0;
    }
    if (resource_id == 0) {
        transfer_amount = py_min(ucfg->CARGO_SPACE - u->ice, amount);
        u->ice += transfer_amount;
    } else if (resource_id == 1) {
        transfer_amount = py_min(ucfg->CARGO_SPACE - u->ore, amount);
        u->ore += transfer_amount;
    } else if (resource_id == 2) {
        transfer_amount = py_min(ucfg->CARGO_SPACE - u->water, amount);
        u->water += transfer_amount;
    } else if (resource_id == 3) {
        transfer_amount = py_min(ucfg->CARGO_SPACE - u->metal, amount);
        u->metal += transfer_amount;
    } else if (resource_id == 4) {
        transfer_amount = py_min(ucfg->BATTERY_CAPACITY - u->power, amount);
        u->power += transfer_amount;
    }
    return transfer_amount;
}

/* Unit.sub_resource: subtract/transfer out as much as you min(have, request). */
int64_t unit_sub_resource(lux_unit *u, int64_t resource_id, int64_t amount)
{
    int64_t transfer_amount = 0;
    if (amount < 0) {
        amount = 0;
    }
    if (resource_id == 0) {
        transfer_amount = py_min(u->ice, amount);
        u->ice -= transfer_amount;
    } else if (resource_id == 1) {
        transfer_amount = py_min(u->ore, amount);
        u->ore -= transfer_amount;
    } else if (resource_id == 2) {
        transfer_amount = py_min(u->water, amount);
        u->water -= transfer_amount;
    } else if (resource_id == 3) {
        transfer_amount = py_min(u->metal, amount);
        u->metal -= transfer_amount;
    } else if (resource_id == 4) {
        transfer_amount = py_min(u->power, amount);
        u->power -= transfer_amount;
    }
    return transfer_amount;
}
