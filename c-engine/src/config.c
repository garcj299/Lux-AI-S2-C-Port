/* config.c: config.py, EnvConfig and UnitConfig. */
#include <string.h>

#include "internal.h"

/* EnvConfig() with its default ROBOTS. */
void lux_config_default(lux_config *cfg)
{
    memset(cfg, 0, sizeof(*cfg));

    cfg->max_episode_length = 1000;
    cfg->map_size = 64;
    cfg->verbose = 1;
    cfg->validate_action_space = 1;

    cfg->max_transfer_amount = 3000;
    cfg->MIN_FACTORIES = 4;
    cfg->MAX_FACTORIES = 10;
    cfg->CYCLE_LENGTH = 50;
    cfg->DAY_LENGTH = 30;
    cfg->UNIT_ACTION_QUEUE_SIZE = 20;

    cfg->MAX_RUBBLE = 100;
    cfg->FACTORY_RUBBLE_AFTER_DESTRUCTION = 50;
    cfg->INIT_WATER_METAL_PER_FACTORY = 150;
    cfg->INIT_POWER_PER_FACTORY = 1000;

    cfg->MIN_LICHEN_TO_SPREAD = 20;
    cfg->LICHEN_LOST_WITHOUT_WATER = 1;
    cfg->LICHEN_GAINED_WITH_WATER = 1;
    cfg->MAX_LICHEN_PER_TILE = 100;
    cfg->POWER_PER_CONNECTED_LICHEN_TILE = 1;
    cfg->LICHEN_WATERING_COST_FACTOR = 10;

    cfg->BIDDING_SYSTEM = 1;

    cfg->FACTORY_PROCESSING_RATE_WATER = 100;
    cfg->ICE_WATER_RATIO = 4;
    cfg->FACTORY_PROCESSING_RATE_METAL = 50;
    cfg->ORE_METAL_RATIO = 5;

    cfg->FACTORY_CHARGE = 50;
    cfg->FACTORY_WATER_CONSUMPTION = 1;

    cfg->POWER_LOSS_FACTOR = 0.5;

    lux_unit_config *light = &cfg->ROBOTS[LUX_LIGHT];
    light->METAL_COST = 10;
    light->POWER_COST = 50;
    light->INIT_POWER = 50;
    light->CARGO_SPACE = 100;
    light->BATTERY_CAPACITY = 150;
    light->CHARGE = 1;
    light->MOVE_COST = 1;
    light->RUBBLE_MOVEMENT_COST = 0.05;
    light->DIG_COST = 5;
    light->SELF_DESTRUCT_COST = 10;
    light->DIG_RUBBLE_REMOVED = 2;
    light->DIG_RESOURCE_GAIN = 2;
    light->DIG_LICHEN_REMOVED = 10;
    light->RUBBLE_AFTER_DESTRUCTION = 1;
    light->ACTION_QUEUE_POWER_COST = 1;

    lux_unit_config *heavy = &cfg->ROBOTS[LUX_HEAVY];
    heavy->METAL_COST = 100;
    heavy->POWER_COST = 500;
    heavy->INIT_POWER = 500;
    heavy->CARGO_SPACE = 1000;
    heavy->BATTERY_CAPACITY = 3000;
    heavy->CHARGE = 10;
    heavy->MOVE_COST = 20;
    heavy->RUBBLE_MOVEMENT_COST = 1;
    heavy->DIG_COST = 60;
    heavy->SELF_DESTRUCT_COST = 100;
    heavy->DIG_RUBBLE_REMOVED = 20;
    heavy->DIG_RESOURCE_GAIN = 20;
    heavy->DIG_LICHEN_REMOVED = 100;
    heavy->RUBBLE_AFTER_DESTRUCTION = 10;
    heavy->ACTION_QUEUE_POWER_COST = 10;
}

int lux_config_check(const lux_config *cfg)
{
    if (cfg == NULL) {
        return LUX_ERR_ARGUMENT;
    }
    if (cfg->map_size < 1 || cfg->map_size > LUX_MAX_MAP_SIZE) {
        return LUX_ERR_CONFIG;
    }
    if (cfg->MAX_FACTORIES < 0 || cfg->MAX_FACTORIES > LUX_MAX_FACTORIES_PER_TEAM) {
        return LUX_ERR_CONFIG;
    }
    if (cfg->UNIT_ACTION_QUEUE_SIZE < 0 || cfg->UNIT_ACTION_QUEUE_SIZE > LUX_MAX_QUEUE) {
        return LUX_ERR_CONFIG;
    }
    if (!cfg->validate_action_space || !cfg->BIDDING_SYSTEM) {
        return LUX_ERR_CONFIG;
    }
    /* destroying robots in any order leaves the same rubble only when they add some */
    if (cfg->ROBOTS[LUX_LIGHT].RUBBLE_AFTER_DESTRUCTION < 0 || cfg->ROBOTS[LUX_HEAVY].RUBBLE_AFTER_DESTRUCTION < 0) {
        return LUX_ERR_CONFIG;
    }
    /* divisors: Python would raise ZeroDivisionError */
    if (cfg->CYCLE_LENGTH == 0 || cfg->ICE_WATER_RATIO == 0 || cfg->ORE_METAL_RATIO == 0 ||
        cfg->LICHEN_WATERING_COST_FACTOR == 0) {
        return LUX_ERR_CONFIG;
    }
    return LUX_OK;
}
