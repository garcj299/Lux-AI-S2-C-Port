/* act_space.c: spaces/act_space.py, the main game's action space and its contains(). */
#include "internal.h"

/* get_act_space(...).contains(actions): PartialDict.contains with an
 * ActionsQueue(Box) per unit of the team and a Discrete(3) per factory.
 * 1 if the player's actions fit the space. Only the verdict matters: the reason
 * is only logged. */
int act_space_contains(const lux_state *s, int32_t team, const lux_agent_commands *cmds)
{
    const lux_config *cfg = &s->env_cfg;
    int64_t n_spaces = (int64_t)s->n_units[team] + s->n_factories[team];
    if (cmds->n_commands > n_spaces) {
        return 0;
    }
    for (int32_t i = 0; i < cmds->n_commands; i++) {
        const lux_command *c = &cmds->commands[i];
        if (c->kind == LUX_KEY_UNIT && c->id >= 0 && lux_find_unit(s, team, c->id) >= 0) {
            /* ActionsQueue.contains */
            if (c->n_actions > cfg->UNIT_ACTION_QUEUE_SIZE) {
                return 0;
            }
            for (int32_t k = 0; k < c->n_actions; k++) {
                if (!action_in_space(cfg, &c->actions[k])) {
                    return 0;
                }
            }
        } else if (c->kind == LUX_KEY_FACTORY && c->id >= 0 && lux_find_factory(s, team, c->id) >= 0) {
            /* Discrete(3).contains */
            if (!(c->factory_action >= 0 && c->factory_action < 3)) {
                return 0;
            }
        } else {
            return 0; /* "{k} is not on your team or does not exist" */
        }
    }
    return 1;
}
