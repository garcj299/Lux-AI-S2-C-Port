"""What difftest.py and scenarios.py share: starting a game in Python, playing a turn
in both engines and comparing them, and the tally of what the games exercised.

- new_game(seed, rng, map_size): a Python game just past setup, or None.
- step_both(lib, ws, env, cstate, actions, coverage): one turn in both engines;
  returns the differences as readable lines (empty if none).
- Coverage: counts what happened in the Python engine.
"""
import contextlib
import io

import numpy as np

import loader
import luxc
from luxai_s2.env import LuxAI_S2
from luxai_s2.team import FactionTypes
from luxai_s2.utils.utils import my_turn_to_place_factory

PLAYERS = loader.PLAYERS
FACTIONS = [f.name for f in FactionTypes]


# ---- Setting a game up in Python --------------------------------------------------------


def play_setup(env, rng):
    """The bid and the factory placement, by the Python engine, with random but legal
    choices. Returns False if a player failed (so the game never started).

    Each team spreads its water and metal one of three ways: 150 each like the
    starter bot, at random, or all of it into its first factory (which can then
    last the whole 1000 turns, while the others run dry at once). It places its
    factories anywhere, or, as real bots do, next to ice or to ore."""
    bids = {p: {"faction": rng.choice(FACTIONS), "bid": rng.choice([0, 0, 0, rng.randint(-10, 10)])} for p in PLAYERS}
    spread = {p: rng.choice(["starter", "starter", "random", "hoard"]) for p in PLAYERS}
    near = {p: rng.choice([None, None, "ice", "ore"]) for p in PLAYERS}
    _, _, terminations, _, _ = quiet_step(env, bids)
    if any(terminations.values()):
        return False
    board = env.state.board
    while env.state.real_env_steps < 0:
        actions = {}
        for player in PLAYERS:
            team = env.state.teams[player]
            if team.factories_to_place > 0 and my_turn_to_place_factory(team.place_first, env.state.env_steps):
                spawns = np.argwhere(board.valid_spawns_mask)
                resource = np.argwhere(getattr(board, near[player])) if near[player] else []
                if len(resource):
                    picks = [spawns[rng.randrange(len(spawns))] for _ in range(50)]
                    x, y = min(picks, key=lambda p: np.abs(resource - p).sum(axis=1).min())
                else:
                    x, y = spawns[rng.randrange(len(spawns))]
                if spread[player] == "starter":
                    water, metal = 150, 150
                elif spread[player] == "random":
                    water = rng.randint(0, int(team.init_water))
                    metal = rng.randint(0, int(team.init_metal))
                else:
                    water, metal = int(team.init_water), int(team.init_metal)
                actions[player] = {"spawn": [int(x), int(y)], "water": water, "metal": metal}
            else:
                actions[player] = {}
        _, _, terminations, _, _ = quiet_step(env, actions)
        if any(terminations.values()):
            return False
    return True


def new_game(seed, rng, map_size=None):
    """A Python game with this seed, set up by play_setup, or None if a player failed
    during setup."""
    env_kwargs = {"verbose": 0}
    if map_size:
        env_kwargs["map_size"] = map_size
    env = LuxAI_S2(**env_kwargs)
    env.reset(seed=seed)
    return env if play_setup(env, rng) else None


def quiet_step(env, actions, coverage=None):
    """env.step without the tracebacks Python prints for a player's bad orders. With
    a Coverage, counts the exceptions they show."""
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        result = env.step(actions)
    if coverage is not None:
        coverage.add_exceptions(printed.getvalue())
    return result


# ---- One turn in both engines -------------------------------------------------------------


def step_both(lib, ws, env, cstate, actions, coverage):
    """Plays one turn with these actions in the Python game and in its C copy, then
    compares the step's results and the whole state. Returns the differences as
    readable lines, empty if there are none."""
    commands, keep = loader.commands_from_actions(actions)
    _, rewards, terminations, truncations, _ = quiet_step(env, actions, coverage)
    for player in PLAYERS:
        if truncations[player]:
            coverage.add("lost_all" if not env.state.factories[player] else "malformed")
    result = luxc.StepResult()
    rc = lib.lux_step(cstate, ws, commands, result)
    del keep
    if rc != luxc.OK:
        return [f"lux_step returned {luxc.error_name(rc)}; Python played the turn"]

    report = []
    for t, player in enumerate(PLAYERS):
        if int(rewards[player]) != result.rewards[t]:
            report.append(f"reward of {player}: python {int(rewards[player])}, C {result.rewards[t]}")
        if bool(truncations[player]) != bool(result.failed[t]):
            report.append(f"failed {player}: python {bool(truncations[player])}, C {bool(result.failed[t])}")
        if bool(terminations[player]) != bool(result.done):
            report.append(f"done for {player}: python {bool(terminations[player])}, C {bool(result.done)}")
    return report + loader.compare(loader.snapshot_python(env), loader.snapshot_c(cstate))


# ---- What the games exercised -----------------------------------------------------------


class Coverage:
    """Counts what happened in the Python engine during the run, by wrapping some of
    its functions (they still run unchanged). "No difference" only means something
    for the situations that actually came up.

    Each count has a short key (what --require names) and a label for the report."""

    KINDS = ("move", "transfer", "pickup", "dig", "self_destruct", "recharge", "factory_build", "factory_water")

    # the report's order; zeros are shown, so what never happened is visible
    LABELS = {
        **{
            f"{kind}_{verdict}": f"{kind} orders {text}"
            for kind in KINDS
            for verdict, text in (("ok", "carried out"), ("dropped", "dropped as invalid"))
        },
        "dig_rubble": "digs on rubble",
        "dig_lichen": "digs on lichen",
        "dig_last_lichen": "  ... that dug out a tile's last lichen (quirk 6)",
        "dig_ice": "digs on ice",
        "dig_ore": "digs on ore",
        "dig_full": "  ... into a cargo hold too full for all of it",
        "dig_bare": "digs on bare ground",
        "built": "robots built",
        "self_destructed": "robots self-destructed",
        "crushed": "robots destroyed in collisions",
        "by_power": "collisions decided by power",
        "tie": "  ... a tie: all destroyed",
        "quirk1": "  ... with the wrong second-strongest robot (quirk 1)",
        "built_under": "tiles where a factory built under a standing robot",
        "quirk2": "  ... a light survived two standing heavies (quirk 2)",
        "quirk3": "  ... nobody entered and all standing robots were destroyed (quirk 3)",
        "quirk4_transfer": "transfers by robots destroyed that turn (quirk 4)",
        "quirk4_pickup": "pickups by robots destroyed that turn (quirk 4)",
        "refine_ice": "factories refining ice into water",
        "refine_ore": "factories refining ore into metal",
        "lichen_spread": "factories whose lichen grew past its 12 starting tiles",
        "lichen_connected": "factories earning power from connected lichen",
        "lichen_max": "lichen tiles cut back to MAX_LICHEN_PER_TILE",
        "dry": "factories run dry",
        "lost_all": "players who lost all their factories",
        "malformed": "players failed by a malformed order",
    }

    def __init__(self):
        self.counts = {}

    def add(self, key, n=1):
        self.counts[key] = self.counts.get(key, 0) + n

    def add_exceptions(self, printed):
        """Counts the exceptions in the tracebacks Python printed during a step (it
        prints one for each player whose orders raise while updating queues)."""
        lines = printed.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("Traceback"):
                last = next((l for l in lines[i + 1 :] if l and not l[0].isspace()), "?")
                self.add(f"raised_{last.split(':')[0]}")

    def label(self, key):
        if key.startswith("raised_"):
            return f"  ... with Python raising {key[len('raised_'):]}"
        return self.LABELS.get(key, key)

    def install(self):
        import luxai_s2.env as lux_env
        import luxai_s2.factory as lux_factory

        cov = self
        validate_actions = lux_env.validate_actions
        top_two = lux_env.get_top_two_power_units
        env_cls = lux_env.LuxAI_S2
        factory_cls = lux_factory.Factory
        add_unit = env_cls.add_unit
        destroy_unit = env_cls.destroy_unit
        destroy_factory = env_cls.destroy_factory
        dig = env_cls._handle_dig_actions
        movement = env_cls._handle_movement_actions
        transfer = env_cls._handle_transfer_actions
        pickup = env_cls._handle_pickup_actions
        refine_step = factory_cls.refine_step
        cache_water_info = factory_cls.cache_water_info
        in_movement = [False]

        def counted_validate_actions(env_cfg, state, actions_by_type, verbose=1):
            given = {kind: len(entries) for kind, entries in actions_by_type.items()}
            valid = validate_actions(env_cfg, state, actions_by_type, verbose=verbose)
            for kind, n in given.items():
                cov.add(f"{kind}_ok", len(valid[kind]))
                cov.add(f"{kind}_dropped", n - len(valid[kind]))
            return valid

        def counted_dig(self, actions_by_type):
            board = self.state.board
            for unit, _ in actions_by_type["dig"]:
                x, y = unit.pos.x, unit.pos.y
                ucfg = unit.unit_cfg
                if board.rubble[x, y] > 0:
                    cov.add("dig_rubble")
                elif board.lichen[x, y] > 0:
                    cov.add("dig_lichen")
                    if board.lichen[x, y] <= ucfg.DIG_LICHEN_REMOVED:
                        cov.add("dig_last_lichen")
                elif board.ice[x, y] > 0 or board.ore[x, y] > 0:
                    held = unit.cargo.ice if board.ice[x, y] > 0 else unit.cargo.ore
                    cov.add("dig_ice" if board.ice[x, y] > 0 else "dig_ore")
                    if held + ucfg.DIG_RESOURCE_GAIN > ucfg.CARGO_SPACE:
                        cov.add("dig_full")
                else:
                    cov.add("dig_bare")
            return dig(self, actions_by_type)

        def counted_top_two(units, unit_type):
            most, next_most = top_two(units, unit_type)
            powers = sorted((u.power for u in units if u.unit_type == unit_type), reverse=True)
            cov.add("by_power")
            if most.power == next_most.power:
                cov.add("tie")
            elif next_most.power != powers[1]:
                cov.add("quirk1")
            return most, next_most

        def counted_add_unit(self, team, unit_type, pos):
            cov.add("built")
            return add_unit(self, team, unit_type, pos)

        def counted_destroy_unit(self, unit):
            cov.add("crushed" if in_movement[0] else "self_destructed")
            return destroy_unit(self, unit)

        def counted_destroy_factory(self, factory):
            cov.add("dry")
            return destroy_factory(self, factory)

        def counted_movement(self, actions_by_type):
            units_map = self.state.board.units_map
            crowded = {k: list(v) for k, v in units_map.items() if len(v) > 1}
            cov.add("built_under", len(crowded))
            entered = {
                f"{u.pos.x + lux_env.move_deltas[a.move_dir][0]},{u.pos.y + lux_env.move_deltas[a.move_dir][1]}"
                for u, a in actions_by_type["move"]
                if a.move_dir != 0
            }
            in_movement[0] = True
            try:
                result = movement(self, actions_by_type)
            finally:
                in_movement[0] = False
            for key, before in crowded.items():
                heavies = [u for u in before if u.unit_type.name == "HEAVY"]
                after = self.state.board.units_map.get(key, [])
                if len(heavies) >= 2 and after and after[0].unit_type.name == "LIGHT":
                    cov.add("quirk2")
                if key not in entered and not after:
                    cov.add("quirk3")
            return result

        def counted_transfer(self, actions_by_type):
            for unit, _ in actions_by_type["transfer"]:
                if unit.unit_id not in self.state.units[unit.team.agent]:
                    cov.add("quirk4_transfer")
            return transfer(self, actions_by_type)

        def counted_pickup(self, actions_by_type):
            for unit, _ in actions_by_type["pickup"]:
                if unit.unit_id not in self.state.units[unit.team.agent]:
                    cov.add("quirk4_pickup")
            result = pickup(self, actions_by_type)
            # the last handler: the lichen update comes next, -1 then the clip
            cov.add("lichen_max", int((self.state.board.lichen - 1 > self.env_cfg.MAX_LICHEN_PER_TILE).sum()))
            return result

        def counted_refine_step(self, env_cfg):
            if min(self.cargo.ice, env_cfg.FACTORY_PROCESSING_RATE_WATER) // env_cfg.ICE_WATER_RATIO > 0:
                cov.add("refine_ice")
            if min(self.cargo.ore, env_cfg.FACTORY_PROCESSING_RATE_METAL) // env_cfg.ORE_METAL_RATIO > 0:
                cov.add("refine_ore")
            return refine_step(self, env_cfg)

        def counted_cache_water_info(self, board, env_cfg):
            result = cache_water_info(self, board, env_cfg)
            x, y = int(self.pos.x), int(self.pos.y)
            start = {(x + dx, y + dy) for dx, dy in LICHEN_START}
            if any((int(px), int(py)) not in start for px, py in self.grow_lichen_positions):
                cov.add("lichen_spread")
            if self.connected_lichen_positions:
                cov.add("lichen_connected")
            return result

        lux_env.validate_actions = counted_validate_actions
        lux_env.get_top_two_power_units = counted_top_two
        env_cls.add_unit = counted_add_unit
        env_cls.destroy_unit = counted_destroy_unit
        env_cls.destroy_factory = counted_destroy_factory
        env_cls._handle_dig_actions = counted_dig
        env_cls._handle_movement_actions = counted_movement
        env_cls._handle_transfer_actions = counted_transfer
        env_cls._handle_pickup_actions = counted_pickup
        factory_cls.refine_step = counted_refine_step
        factory_cls.cache_water_info = counted_cache_water_info

    def report(self):
        keys = list(self.LABELS) + sorted(k for k in self.counts if k not in self.LABELS)
        # a failed player's exceptions go right under "players failed by a malformed order"
        raised = [k for k in keys if k.startswith("raised_")]
        keys = [k for k in keys if not k.startswith("raised_")]
        at = keys.index("malformed") + 1
        keys[at:at] = raised
        width = max(len(self.label(k)) for k in keys)
        return [f"  {self.label(k).ljust(width)}  {self.counts.get(k, 0)}" for k in keys]

    def missing(self, keys):
        """The keys among these that never happened."""
        return [k for k in keys if not self.counts.get(k)]


# Factory.cache_water_info's 12 starting tiles, around the factory's 3x3
LICHEN_START = [(0, -2), (-1, -2), (1, -2), (0, 2), (-1, 2), (1, 2), (2, 0), (2, -1), (2, 1), (-2, 0), (-2, -1), (-2, 1)]
