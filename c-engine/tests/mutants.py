"""mutants: deliberately broken copies of the engine, which the tests must catch.

    mutants.py [--jobs N] [NAME ...]

Each mutant changes one rule in a copy of the C sources, in build/mutants/<name>/.
The copy is built, then each part of `make test` runs against it (make's LIB=).
A mutant that every part passes is a hole in the tests: the run prints a table of
which part caught which mutant, and exits 1 if any mutant got through.

Each change is a text replacement that must match the sources exactly once, so
a mutant that no longer applies after an edit fails loudly instead of silently
testing nothing. Run it with `make mutants`.
"""
import argparse
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
TARGETS = ["test-errors", "test-scenarios", "test-games", "test-malformed"]


@dataclass
class Mutant:
    name: str
    what: str
    file: str
    old: str
    new: str


MUTANTS = [
    # ---- collisions ----
    Mutant(
        "true_second",
        "quirk 1 fixed: the true second-strongest robot",
        "src/utils.c",
        "                next = most;\n",
        "                next = most;\n                next_most_power = most_power;\n",
    ),
    Mutant(
        "two_heavies_stand",
        "quirk 2 fixed: one of two standing heavies counts as standing",
        "src/env.c",
        "                        has_heavy_stationary = 0;\n                        break;\n",
        "                        break;\n",
    ),
    Mutant(
        "loss_floor",
        "the survivor loses floor instead of ceil of half the second's power",
        "src/env.c",
        "                int64_t most_power_unit_power_loss =\n                    py_ceil(",
        "                int64_t most_power_unit_power_loss =\n                    py_floor(",
    ),
    # ---- movement and validation ----
    Mutant(
        "move_cost_floor_int",
        "a move costs 1 more only at rubble 99",
        "src/unit.c",
        "    return py_floor((double)ucfg->MOVE_COST + ucfg->RUBBLE_MOVEMENT_COST * (double)rubble_at_target);",
        "    return ucfg->MOVE_COST + (int64_t)(ucfg->RUBBLE_MOVEMENT_COST * (double)rubble_at_target)"
        " + (rubble_at_target == 99);",
    ),
    Mutant(
        "move_cost_after_dig",
        "the move cost is computed after digs",
        "src/env.c",
        "            int64_t power_required = ws->acts[ACT_MOVE][i].power_cost;",
        "            int64_t power_required = unit_move_power_cost(cfg, unit, s->board.rubble[lux_tile(s, tx, ty)]);",
    ),
    Mutant(
        "enter_enemy_factory",
        "robots may move onto enemy factories",
        "src/actions.c",
        "        if (factory_id != -1 && lux_find_factory(s, unit->team_id, factory_id) < 0) {",
        "        if (factory_id != -1 && lux_find_factory(s, unit->team_id, factory_id) < -1) {",
    ),
    Mutant(
        "queue_cost_le",
        "a robot with exactly the queue cost can't update its queue",
        "src/env.c",
        "            if (unit->power < update_power_req) {",
        "            if (unit->power <= update_power_req) {",
    ),
    # ---- transfer and pickup ----
    Mutant(
        "no_dead_transfer",
        "robots destroyed this turn no longer transfer (quirk 4)",
        "src/env.c",
        "        int64_t transfer_amount = ws->amount_list[i];",
        "        int64_t transfer_amount = ws->dead[transfers[i].who.team][transfers[i].who.index] ? 0 : ws->amount_list[i];",
    ),
    Mutant(
        "cargo_no_cap",
        "a robot's ice isn't capped at its cargo space",
        "src/unit.c",
        "        transfer_amount = py_min(ucfg->CARGO_SPACE - u->ice, amount);",
        "        transfer_amount = amount;",
    ),
    # ---- digging ----
    Mutant(
        "dig_lichen_rubble",
        "dug-out lichen leaves the wrong rubble (quirk 6)",
        "src/env.c",
        "                b->rubble[tile] = ucfg->DIG_RESOURCE_GAIN;",
        "                b->rubble[tile] = ucfg->DIG_RUBBLE_REMOVED + 1;",
    ),
    Mutant(
        "dig_ice_gain",
        "a dig on ice gains one more ice",
        "src/env.c",
        "            unit_add_resource(cfg, unit, 0, ucfg->DIG_RESOURCE_GAIN);",
        "            unit_add_resource(cfg, unit, 0, ucfg->DIG_RESOURCE_GAIN + 1);",
    ),
    Mutant(
        "dig_ore_as_ice",
        "a dig on ore gains ice",
        "src/env.c",
        "            unit_add_resource(cfg, unit, 1, ucfg->DIG_RESOURCE_GAIN);",
        "            unit_add_resource(cfg, unit, 0, ucfg->DIG_RESOURCE_GAIN);",
    ),
    # ---- factories ----
    Mutant(
        "refine_ore_ratio",
        "ore refines at one more ore per metal",
        "src/factory.c",
        "    int64_t produced_metal = py_floordiv(max_consumed_ore, cfg->ORE_METAL_RATIO);",
        "    int64_t produced_metal = py_floordiv(max_consumed_ore, cfg->ORE_METAL_RATIO + 1);",
    ),
    Mutant(
        "refine_ice_no_rate",
        "a factory refines all its ice in one turn",
        "src/factory.c",
        "    int64_t max_consumed_ice = py_min(f->ice, cfg->FACTORY_PROCESSING_RATE_WATER);",
        "    int64_t max_consumed_ice = f->ice;",
    ),
    Mutant(
        "dry_at_zero",
        "a factory dies with 0 water left, not below 0",
        "src/env.c",
        "            ws->factory_destroy[i] = factory->water < 0;",
        "            ws->factory_destroy[i] = factory->water <= 0;",
    ),
    Mutant(
        "rubble_under_factories",
        "rubble under factories isn't cleared at the end of the turn",
        "src/env.c",
        "            if (b->factory_occupancy_map[tile] != -1) {\n                b->rubble[tile] = 0;",
        "            if (b->factory_occupancy_map[tile] == -2) {\n                b->rubble[tile] = 0;",
    ),
    # ---- lichen ----
    Mutant(
        "water_plus_one",
        "watering adds 1 lichen instead of 2",
        "src/env.c",
        "            b->lichen[grow_tiles[k]] += 2;",
        "            b->lichen[grow_tiles[k]] += 1;",
    ),
    Mutant(
        "stale_water_check",
        "the watering check ignores its cost",
        "src/actions.c",
        "        int64_t water_cost = factory_water_cost(factory, cfg);",
        "        int64_t water_cost = 0;",
    ),
    Mutant(
        "spread_strict",
        "lichen spreads only above MIN_LICHEN_TO_SPREAD, not at it",
        "src/factory.c",
        "            if ((adj_strain == -1 && pos_lichen >= MIN_LICHEN_TO_SPREAD) ||",
        "            if ((adj_strain == -1 && pos_lichen > MIN_LICHEN_TO_SPREAD) ||",
    ),
    Mutant(
        "strains_mix",
        "lichen grows next to another factory's lichen",
        "src/factory.c",
        "            if ((adj_strain != -1 && adj_strain != strain_id) ||",
        "            if ((adj_strain == -2 && adj_strain != strain_id) ||",
    ),
    Mutant(
        "lichen_cap_plus_one",
        "lichen is cut back to MAX_LICHEN_PER_TILE + 1",
        "src/env.c",
        "            b->lichen[tile] = py_clip(b->lichen[tile], 0, cfg->MAX_LICHEN_PER_TILE);",
        "            b->lichen[tile] = py_clip(b->lichen[tile], 0, cfg->MAX_LICHEN_PER_TILE + 1);",
    ),
    Mutant(
        "lichen_power_grow",
        "factories earn power for every lichen tile they can grow on, not just the connected ones",
        "src/env.c",
        "f->connected_lichen_count * cfg->POWER_PER_CONNECTED_LICHEN_TILE",
        "f->grow_lichen_count * cfg->POWER_PER_CONNECTED_LICHEN_TILE",
    ),
]


# an unbroken copy, built and tested the same way: if the tests fail on it, a
# "caught" would mean nothing
CONTROL = Mutant("control", "nothing: the unbroken engine", "", "", "")


def build(mutant):
    """Builds the mutant in build/mutants/<name>/; returns its library."""
    root = ENGINE / "build" / "mutants" / mutant.name
    for part in ("src", "include"):
        shutil.copytree(ENGINE / part, root / part, dirs_exist_ok=True)
    shutil.copy(ENGINE / "Makefile", root / "Makefile")
    if mutant is not CONTROL:
        path = root / mutant.file
        text = path.read_text()
        if text.count(mutant.old) != 1:
            raise SystemExit(f"{mutant.name}: its text appears {text.count(mutant.old)} times in {mutant.file}, not once")
        path.write_text(text.replace(mutant.old, mutant.new))
    subprocess.run(["make", "-s", "-C", str(root), "build/liblux.so"], check=True, capture_output=True)
    return root / "build" / "liblux.so"


def run(mutant):
    """Which of make test's parts catch the mutant: {target: True if it failed}."""
    lib = build(mutant)
    return {
        target: subprocess.run(
            ["make", "-s", "-C", str(ENGINE), target, f"LIB={lib}"], capture_output=True
        ).returncode
        != 0
        for target in TARGETS
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check that the tests catch deliberately broken engines.")
    parser.add_argument("names", nargs="*", help="only these mutants")
    parser.add_argument("--jobs", type=int, default=8, help="mutants tested at once (default 8)")
    args = parser.parse_args(argv)
    chosen = [m for m in MUTANTS if not args.names or m.name in args.names]
    with ThreadPoolExecutor(args.jobs) as pool:
        control, *results = pool.map(run, [CONTROL, *chosen])
    if any(control.values()):
        failing = [t for t, failed in control.items() if failed]
        raise SystemExit(f"the tests fail on the unbroken engine ({', '.join(failing)}): fix that first")

    columns = [t.removeprefix("test-") for t in TARGETS]
    width = max(len(m.name) for m in chosen)
    print(f"{'mutant'.ljust(width)}  " + "  ".join(c.ljust(10) for c in columns) + "  what it breaks")
    survivors = []
    for mutant, caught in zip(chosen, results):
        cells = "  ".join(("caught" if caught[t] else "-").ljust(10) for t in TARGETS)
        print(f"{mutant.name.ljust(width)}  {cells}  {mutant.what}")
        if not any(caught.values()):
            survivors.append(mutant.name)
    if survivors:
        print(f"NOT CAUGHT: {', '.join(survivors)}")
        return 1
    print(f"all {len(chosen)} mutants caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
