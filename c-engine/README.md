# c-engine

The C port of the Lux AI Season 2 engine. It is meant to be **exact**: for the same board and the same actions it must give the same next board as the Python engine (`luxai_s2` 3.0.1, installed in the repository's `.venv/`), invalid actions included.

**Status (2026-10-23):** the turn rules of the main game are translated, function by function, and build without warnings. Nothing has been run against the Python engine yet: that is the differential tester's job, and it isn't written. Until it passes, this is a careful translation, not a proven one.

## Build

    make          # build/liblux.a and build/liblux.so

Needs gcc (C11) on x86-64. The rules' few floats must round exactly as Python's, so the build turns off fused multiply-add (`-ffp-contract=off`), and the code refuses to compile where doubles are evaluated in wider precision (`FLT_EVAL_METHOD != 0`, e.g. 32-bit x87).

## What is ported

One C file per Python file, and every C function names the Python function it translates, so the two read side by side. Fields keep the Python attribute names.

| C | Python (`luxai_s2/`) | What |
|---|---|---|
| `include/lux.h` | | The public API and the state's layout |
| `src/env.c` | `env.py` | `step()` for a main-game turn: queue updates, collecting actions, every action handler, lichen, refining, power, rewards; `add_unit`, `destroy_unit`, `destroy_factory` |
| `src/actions.c` | `actions.py` | `format_action_vec`, `format_factory_action`, `move_deltas`, `validate_actions` |
| `src/act_space.c` | `spaces/act_space.py` | The main game's action space check (`PartialDict`, `ActionsQueue`, `Box`, `Discrete`) |
| `src/unit.c` | `unit.py` | `Unit`: `repeat_action`, `move_power_cost`, `add_resource`, `sub_resource` |
| `src/factory.c` | `factory.py` | `Factory` and `compute_water_info` (lichen growth) |
| `src/board.c` | `map/board.py` | `get_factory_at`, and `units_map` |
| `src/utils.c` | `utils/utils.py` | `is_day`, `get_top_two_power_units` |
| `src/config.c` | `config.py` | `EnvConfig` defaults |
| `src/state.c` | `state/state.py` | `real_env_steps`, allocation, lookups |
| `src/pymath.h` | | Python's `//`, `%`, slices, `math.floor`/`ceil` |

**Not ported yet:**
- **The setup phase:** the bid and the factory placement (`_handle_bid`, `_handle_factory_placement_step`, `add_factory`). `lux_step` returns `LUX_ERR_EARLY_GAME` for those turns.
- **The map generator:** see the map engine note in the Onodrim. A game reaches C already past the setup phase.
- **Loading a state from Python, and the differential tester:** nothing fills a `lux_state` from Python yet (see "Loading a state" below for what it must copy).
- **Not part of the rules, so not ported:** observations, rendering, logging (`verbose`), and `collect_stats`.

## Design

**Layout from JUX, rules from Python.** JUX (the JAX engine) shaped the memory layout:
- fixed-size arrays per team, kept sorted by id;
- grids indexed `[x][y]`, exactly like NumPy's row-major arrays, so a board copies in unchanged.

The rules were taken only from the Python code. JUX's own rules differ wherever the Python code is quirky. Its collisions use the true second-strongest robot, count only moving robots and never let a light robot survive a heavy one. JUX matches the reference only for valid actions.

**Every value is a 64-bit integer**, as the NumPy arrays and Python ints it copies (`dtype=int`). The two doubles in the config (`RUBBLE_MOVEMENT_COST`, `POWER_LOSS_FACTOR`) are used exactly where Python mixes floats in. Narrower types would be faster and are a later optimisation, once the tester can prove them.

**Fixed capacities that can't be reached.**
- **Robots:** at most one per tile at the end of a turn, plus one build per factory during it: `LUX_MAX_UNITS_PER_TEAM` = 4,096 + 10.
- **Factory queue:** never holds more than 2.
- **Movement scratch:** at most 4 robots enter a tile and 2 stay.

If a bound were ever passed, the step returns `LUX_ERR_CAPACITY` instead of playing a different game.

**State and scratch are separate.**
- `lux_state` is plain data that can be copied and compared. It holds only what the rules read.
- `lux_workspace` holds a step's scratch memory: one per thread, reused across games.

**Python's dict orders are kept.**
- Robots and factories stay in each team's creation order, as Python's `OrderedDict`s keep them. That order decides who acts first, and which robot is "next strongest" in a collision.
- Python's `units_map` isn't stored. At the end of every turn it holds exactly the robots, one per tile, so each step rebuilds it.
- The order in which the movement code visits tiles is not kept. Each tile's collision depends only on its own robot list, whose order *is* kept, and destroying robots gives the same result in any order.

**Destroyed robots are marked, not removed, until the step ends.** Python deletes them at once, but the transfer and pickup handlers still run the actions they collected earlier in the turn (see below).

**Supported config:** anything `lux_config_check` accepts.
- `validate_action_space` must be on, as it is on Kaggle. Without it, Python lets out-of-range numbers into the queues, then crashes on some and wraps others through NumPy's negative indexing.
- The map can't be larger than 64×64, there can't be more than 10 factories per team, and queues can't be longer than 20.

## Quirks kept on purpose

The port copies what the Python code does, not what the rules document says. These behaviours change results and are easy to "fix" by accident:

1. **Collision "second strongest" (`get_top_two_power_units`):**
   - **Wrong robot sometimes:** when a new strongest robot is found, the previous strongest becomes "next", but the "next" power isn't updated. With heavies of power 10, 20 and 5 in list order, the pair is (20, 5), not (20, 10). Both the tie check and the survivor's power loss read that robot.
   - **Includes standing robots:** stationary robots of the same type count too, not only moving ones.
2. **Two stationary heavies on one tile, one light entering:** the light survives and both heavies are destroyed. This happens when a factory builds a heavy under a heavy standing on its center.
3. **Two robots of the same type standing still on one tile, with nobody entering, are both destroyed.** For example, a factory builds a light under a light standing on its center. With one heavy and one light, the heavy survives.
4. **Robots destroyed in a collision still act later that turn:**
   - Their transfers are still delivered.
   - Their pickups still take from the factory, and what they take is lost.
5. **Watering checks last turn's cost but pays this turn's:** validation reads the lichen tile count from the previous turn's `cache_water_info`; the watering recomputes it.
6. **Digging out the last lichen on a tile** leaves `DIG_RESOURCE_GAIN` rubble (2 for a light, 20 for a heavy).
7. **A move's power cost is fixed at validation,** from the rubble before this turn's digs.
8. **Lichen of destroyed factories still scores** for their team (`factory_strains` keeps every factory ever placed).
9. **A team that sends a malformed action fails:**
   - Its queue updates still happen (and cost power), but none of its robots or factories act that turn.
   - The game ends after that turn, and the failed team scores −1000.
   - A queue longer than 20 is cut to 20, and still fails the team.
10. **Transfers:**
    - They can go to enemy robots.
    - A transfer to a tile with neither an own factory nor a robot is lost.
    - A robot that transfers to its own tile while standing on its own factory gives it to the factory.
11. **Rubble under factories is reset to 0 at the end of every turn,** including rubble just added there by destroyed robots.

## Loading a state

Nothing loads one yet; this is what the future loader must copy from a live Python env (`env.state`), field by field:
- **Top-level fields:** `env_steps`, `global_id`, `done = 0`.
- **Board** (`state.board`): each grid's row-major data as int64, and `factories_per_team`.
- **Robots** (`state.units[player]`), in dict order:
  - the number in `unit_N`;
  - `unit_type`, `pos`, `power`, `cargo`;
  - `action_queue` as each action's `state_dict()`.
- **Factories** (`state.factories[player]`), in dict order:
  - `num_id`, `pos`, `power`, `cargo`;
  - `action_queue` as each action's `state_dict()` (0, 1 or 2);
  - `grow_lichen_count = len(f.grow_lichen_positions)`, `connected_lichen_count = len(f.connected_lichen_positions)`.

  The two counts must come from the live factory objects: observations don't carry them, and `State.from_obs` resets them to 0, which changes the next turn's watering check.
- **Teams:**
  - `team_id`, `faction.value.faction_id`, `init_water`, `init_metal`;
  - `factories_to_place`, `factory_strains`, `place_first`, `bid`.

**Each turn's actions dict:**
- **Key kind:** a key containing `"factory"` is a factory key, else one containing `"unit"` is a robot key, else it's another kind.
- **Key id:** `N` if the key is exactly `unit_N` or `factory_N`, and −1 otherwise.
- **The caller's job:** Python-level type errors (a 1-D action array, floats, strings, missing keys) can't be written in C, so the caller has to deal with them.
