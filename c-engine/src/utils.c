/* utils.c: utils/utils.py. */
#include "internal.h"

int is_day(const lux_config *cfg, int64_t env_step)
{
    return py_mod(env_step, cfg->CYCLE_LENGTH) < cfg->DAY_LENGTH;
}

/* get_top_two_power_units, as written. When a new strongest unit is found, the old
 * strongest becomes "next" but next_most_power keeps its old value. So "next" can
 * end up a weaker unit than the real second strongest: with heavies of power 10,
 * 20, 5 in that order it returns (20, 5), not (20, 10). Callers read both units'
 * power, so this changes ties and power losses. Kept as is. */
void get_top_two_power_units(lux_state *s, const lux_ref *units, int32_t n_units, int32_t unit_type,
                             lux_ref *most_power_unit, lux_ref *next_most_power_unit)
{
    lux_ref most = units[0];
    int64_t most_power = -1;
    lux_ref next = units[1];
    int64_t next_most_power = -1;
    for (int32_t i = 0; i < n_units; i++) {
        const lux_unit *u = lux_unit_at(s, units[i]);
        if (u->unit_type == unit_type) {
            if (u->power > most_power) {
                next = most;
                most = units[i];
                most_power = u->power;
            } else if (u->power >= next_most_power) {
                /* >= check since we want to top 2 power units which can tie */
                next = units[i];
                next_most_power = u->power;
            }
        }
    }
    *most_power_unit = most;
    *next_most_power_unit = next;
}
