# c-engine

The C port of the Lux AI Season 2 engine. It is meant to be **exact**: for the same board and the same actions it must give the same next board as the Python engine (`luxai_s2` 3.0.1, installed in the repository's `.venv/`), invalid actions included.

**Status (2026-10-23):** the turn rules of the main game are translated, function by function, and match the Python engine on everything tried so far:
- **Random games:** 1,200 games and 347,718 turns, every turn identical: 600 with every order type, 300 with malformed orders mixed in, 300 on the 48×48 map (107 of them played all 1,000 turns). They dig ice and ore about 390,000 times, refine about 60,000 times, and grow lichen past its starting tiles and up to its cap.
- **Scenarios:** 15 hand-built turns for the rare cases, identical too. Quirk 1 never came up in the random games above; the scenarios are what test it.
- **The error contract:** 48 checks that a refused step leaves the state untouched.
- **Broken engines:** 22 deliberately broken copies of the engine, each caught by the tests (see below).
- **Sanitizers:** the tests pass on a build with AddressSanitizer and UBSan.

Passing tests are evidence, not proof: they only cover the situations they reach. The tally each run prints says which those were, and `make test` fails if a rule it relies on stops coming up (see "What the games exercised").

## Build and test

    make                # build/liblux.a and build/liblux.so
    make test           # the error contract, the scenarios, 20 random games, 40 with malformed orders (~30 s)
    make test-asan      # the same tests on a build with AddressSanitizer and UBSan
    make mutants        # deliberately broken engines, which the tests must all catch (~1 min)

Needs gcc (C11) on x86-64, and the repository's `.venv` for the tests. The rules' few floats must round exactly as Python's, so the build turns off fused multiply-add (`-ffp-contract=off`), and the code refuses to compile where doubles are evaluated in wider precision (`FLT_EVAL_METHOD != 0`, e.g. 32-bit x87). Those flags are in `LUX_CFLAGS`, after `CFLAGS`, so `make CFLAGS=-O3` changes the optimisation without dropping them.

`make test LIB=path/to/liblux.so` tests another build of the library: `test-asan` and `mutants` use it.

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
| `src/config.c` | `config.py` | `EnvConfig` defaults, and the configs the port supports |
| `src/state.c` | `state/state.py` | `real_env_steps`, allocation, lookups, the layout table, the workspace reset |
| `src/check.c` | | What `lux_step` checks before changing anything (see "Design") |
| `src/pymath.h` | | Python's `//`, `%`, slices, `math.floor`/`ceil`, NumPy's `clip` |

**Not ported yet:**
- **The setup phase:** the bid and the factory placement (`_handle_bid`, `_handle_factory_placement_step`, `add_factory`). `lux_step` returns `LUX_ERR_EARLY_GAME` for those turns. When ported, it goes in its own `src/env_setup.c`, so `env.c` stays the turn.
- **The map generator:** see the map engine note in the Onodrim. A game reaches C already past the setup phase.
- **Not part of the rules, so not ported:** observations, rendering, logging (`verbose`), and `collect_stats`.

## The tests

All of them compare the C engine with the Python engine, through the ctypes bindings in `tests/luxc.py`. Those check, when loaded, every structure, field and constant they hold against the table the library reports (`lux_layout`), by name.

### Random games (`tests/difftest.py`)

    ../.venv/bin/python tests/difftest.py --help        # from c-engine/

1. **Setup in Python:** the Python engine plays the bid and the factory placement (random but legal choices), since C doesn't have them yet. Teams place their factories anywhere, or next to ice or ore, as real bots do.
2. **Copy into C:** `tests/loader.py` copies the game into C, and the copy is compared with Python before any turn.
3. **Lockstep:** both engines then play the same orders. After every turn the whole game is compared: the board, every unit and factory with its queue, the teams, the rewards, who failed and whether the game ended.
4. **Stop at the first difference:** the run prints the difference and the command that repeats it. A seed and the options always give the same games.

**The orders it sends.** Each robot and factory gets a role when first seen:
- **Random** (most of them): random values in range, mostly small. Many are impossible on the board (moving off the map, digging without power): those test validation. "Herding" keeps these robots near their factories, and "rally points" send several onto one tile in the same turn; without them, collisions decided by power almost never happen.
- **Prospectors** (robots): walk to the nearest ice or ore, dig until they hold their load or run low on power, walk home, hand the load to the factory and pick up power. Random orders alone never dig a resource: robots must first walk to one.
- **Gardeners** (factories, mostly the ones holding a lot of water): water every turn they can afford it. Lichen spreads only after about 20 waterings in a row, which random orders never give.

The options:
- `--orders` limits the order types, down to `none`: the step's own rules only (lichen, refining, power). Prospectors need move, transfer, pickup and dig; gardeners need water.
- `--malformed P` slips a malformed order in on some turns: a key that isn't the player's, an out-of-range number, a queue over 20, a bad factory action. Each one ends the game, as it does in Python.
- `--map-size 48` plays the smaller map. `--lib` tests another build.

**What else it checks.**
- **The C engine's assumptions:** every turn, that Python's `units_map` holds exactly the living units, one per tile, and that both teams' units and factories are in id order.
- **What the games exercised:** at the end it prints a tally of what happened, counted by wrapping the Python engine's functions (they still run unchanged): orders carried out and dropped by type, digs on rubble, lichen, ice, ore and bare ground, refining, lichen spreading and reaching its cap, robots built and destroyed, collisions decided by power, each collision quirk below, factories run dry, and players failed (with the exception Python raised). "No difference" only means something for situations that came up. `--require KEYS` fails the run when one of those tally lines stays at 0: `make test` requires every line that 20 games always reach (the list is in the `Makefile`).

### Scenarios (`tests/scenarios.py`)

Some cases are too rare for random games, such as three heavies of different power entering one tile in the order that triggers quirk 1. Each scenario builds one of these on a game just past setup (robots placed through Python's own `add_unit`, with chosen power and cargo), plays one turn in both engines and compares everything. It also checks that its case really happened in Python, so a scenario can't silently stop testing what it is named after. They cover:
- quirk 1, for heavies and for lights;
- a tie, for heavies and for lights;
- quirks 2, 3 and 4 (transfer and pickup), and a heavy and a light standing on one tile;
- the watering check reading last turn's cost;
- digging out the last lichen, and digging into a full cargo hold;
- a move's cost fixed before digs, and a move onto an enemy factory;
- a transfer to an enemy robot.

### The error contract (`tests/errors.py`)

`lux_step` is all or nothing (see "Design"). Each case copies a real game into C, breaks one thing (an invariant, an argument, the config), steps, and checks the code returned and that not one byte of the state changed: 48 checks. The unbroken game must step, so each refusal is down to its break, and a normal turn on the same workspace afterwards must still match Python.

### Broken engines (`tests/mutants.py`)

Are the tests able to fail? `make mutants` builds 22 copies of the engine, each with one rule changed, runs each part of `make test` against each, and fails if any copy gets through. An unbroken copy is run the same way first: if the tests fail on it, the table would mean nothing.

| Broken engine | Error contract | Scenarios | 20 games | Malformed games |
|---|---|---|---|---|
| Quirk 1 "fixed" (the true second-strongest robot) | | caught | | |
| Quirk 2 "fixed" (one of two standing heavies counts as standing) | | caught | caught | caught |
| The survivor loses floor, not ceil, of half the second's power | | | caught | |
| A move costs 1 more only at rubble 99 | | | caught | caught |
| The move cost is computed after digs | | caught | caught | caught |
| Robots may move onto enemy factories | | caught | caught | |
| A robot with exactly the queue cost can't update its queue | | | caught | caught |
| Robots destroyed this turn no longer transfer (quirk 4) | | caught | caught | |
| A robot's ice isn't capped at its cargo space | | caught | caught | |
| Dug-out lichen leaves the wrong rubble (quirk 6) | | caught | | |
| A dig on ice gains one more ice | | | caught | caught |
| A dig on ore gains ice | | | caught | caught |
| Ore refines at one more ore per metal | | | caught | caught |
| A factory refines all its ice in one turn | | | caught | caught |
| A factory dies with 0 water left, not below 0 | | | caught | caught |
| Rubble under factories isn't cleared | | caught | caught | caught |
| Watering adds 1 lichen instead of 2 | | | caught | caught |
| The watering check ignores its cost (quirk 5) | | caught | | |
| Lichen spreads only above `MIN_LICHEN_TO_SPREAD`, not at it | | | caught | caught |
| Lichen grows next to another factory's lichen | | | caught | caught |
| Lichen is cut back to `MAX_LICHEN_PER_TILE + 1` | | | caught | |
| Factories earn power for every tile lichen can grow on | caught | caught | caught | caught |

Each part catches mutants the others miss: the scenarios are why quirks 1, 5 and 6 are caught at all. Before the prospectors and gardeners, the dig, refining and lichen rows got through `make test`.

### Not done yet

- **Comparing after each rule inside a turn:** today the tester compares whole turns.
- **Shrinking a failing game** to the fewest orders that still show the difference.
- **Saved reference runs**, so C can be checked without Python running.
- **Speed:** a state is 8.7 MB (mostly empty robot slots), and every step clears about 80 KB of workspace. Both are for the speed step to measure. So is running test games in parallel.

## Design

**Layout from JUX, rules from Python.** JUX (the JAX engine) shaped the memory layout:
- fixed-size arrays per team, kept sorted by id;
- grids indexed `[x][y]`, exactly like NumPy's row-major arrays, so a board copies in unchanged.

The rules were taken only from the Python code. JUX's own rules differ wherever the Python code is quirky. Its collisions use the true second-strongest robot, count only moving robots and never let a light robot survive a heavy one. JUX matches the reference only for valid actions.

**Every value is a 64-bit integer**, as the NumPy arrays and Python ints it copies (`dtype=int`). The two doubles in the config (`RUBBLE_MOVEMENT_COST`, `POWER_LOSS_FACTOR`) are used exactly where Python mixes floats in. Narrower types would be faster and are a later optimisation, once the tester can prove them. Numbers far beyond any game's (where 64 bits would overflow, which Python ints never do) are not supported.

**All or nothing.** Every state the Python engine reaches between two turns keeps some invariants, and the step relies on them. `src/check.c` checks them all before the step changes anything:
- units on the map, at most one per tile, in id order, with known types, queues no longer than `UNIT_ACTION_QUEUE_SIZE` and holding only actions inside the action space;
- factories in id order with empty queues, and `factory_occupancy_map` holding each one's id on exactly its 3×3 tiles and -1 everywhere else;
- every id given out before `global_id`, and counts within the capacities;
- the arguments: no unit or factory named twice, counts and pointers that hold together.

With those, nothing inside the step can fail. So a step is either refused with an error code, leaving the state untouched, or runs to its end. Where the step relies on an invariant, an `assert` names the Python exception it rules out. They stay on in every build, as a second guard.

**Fixed capacities that can't be reached.**
- **Robots:** at most one per tile between turns, plus one build per factory during a turn: `LUX_MAX_UNITS_PER_TEAM` = 4,096 + 10.
- **Factory queue:** empty at the start of every turn (each turn's action is popped the same turn), so it holds at most 1. Only a failed team keeps its last action, and that ends the game.
- **Movement scratch:** at most 4 robots enter a tile and 2 stay.

**State and scratch are separate.**
- `lux_state` is plain data that can be copied and compared. It holds only what the rules read.
- `lux_workspace` holds a step's scratch memory: one per thread, reused across games. Each step starts by clearing what it reads before writing (`workspace_reset`), so no step depends on what an earlier one left. The lichen search clears its "seen" set by moving a counter on instead of clearing every tile, since it runs for every factory every turn.

**Python's dict orders are kept.**
- Robots and factories stay in each team's creation order, as Python's `OrderedDict`s keep them. That order decides who acts first, and which robot is "next strongest" in a collision.
- Python's `units_map` isn't stored. At the end of every turn it holds exactly the robots, one per tile, so each step rebuilds it.
- The order in which the movement code visits tiles is not kept. Each tile's collision depends only on its own robot list, whose order *is* kept. Destroying a robot changes only its own tile's rubble, lichen and strain, which no collision reads, and adding rubble capped with `min` gives the same result in any order. So each tile's losers are destroyed as soon as it is resolved.

**Destroyed robots are marked, not removed, until the step ends.** Python deletes them at once, but the transfer and pickup handlers still run the actions they collected earlier in the turn (quirk 4).

**Supported config:** anything `lux_config_check` accepts.
- `validate_action_space` must be on, as it is on Kaggle. Without it, Python lets out-of-range numbers into the queues, then crashes on some and wraps others through NumPy's negative indexing.
- `BIDDING_SYSTEM` must be on: the other setting is untested, so not claimed.
- `RUBBLE_AFTER_DESTRUCTION` can't be negative: the rubble a collision leaves would then depend on the order Python destroys the robots in, which is a set's.
- The map can't be larger than 64×64, there can't be more than 10 factories per team, and queues can't be longer than 20. Divisors can't be 0.
- The caps on lichen and rubble work as NumPy's `clip` does, even for odd values: `clip(0, hi)` with a negative `hi` gives `hi`.

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

`tests/loader.py` copies a live Python env into C: `snapshot_python(env)` reads every field a `lux_state` holds, and `load_state` writes that snapshot into C, the inverse of `snapshot_c`. The snapshot is the one list of what is copied. One trap: each factory's `grow_lichen_count` and `connected_lichen_count` must come from the live factory objects (`len(grow_lichen_positions)`, `len(connected_lichen_positions)`). Observations don't carry them, and `State.from_obs` resets them to 0, which changes the next turn's watering check.

**Each turn's actions dict** (`commands_from_actions`):
- **Key kind:** a key containing `"factory"` is a factory key, else one containing `"unit"` is a robot key, else it's another kind.
- **Key id:** `N` if the key is exactly `unit_N` or `factory_N` (written as Python writes ints, and small enough for 64 bits), and −1 otherwise.
- **Values:** robot actions as `(n, 6)` integer arrays (or empty arrays), factory actions as one integer. Python takes other forms too (lists, 1-D arrays, floats) and treats them in ways a `lux_command` can't express, so the loader raises `TypeError` for them rather than guess.
- **An empty dict:** Python's `step({})` raises "No actions given"; `lux_step` with no commands for either player plays the turn.
