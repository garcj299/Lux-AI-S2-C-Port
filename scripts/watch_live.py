"""Watch a Lux AI S2 match live in the engine's built-in pygame window.

Both players are the official Python starter bot (third_party/Lux-Design-S2/kits/python).
The bots get the same observations the official runner (luxai_runner/episode.py) sends them,
but run inside this process instead of as subprocesses.

    .venv/bin/python scripts/watch_live.py --seed 42 --delay 0.05

Close the window or press Ctrl+C to stop.
"""
import argparse
import dataclasses
import importlib.util
import json
import os
import sys
import time
from argparse import Namespace

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import numpy as np
import pygame
from luxai_runner.utils import to_json
from luxai_s2.env import LuxAI_S2

KIT_DIR = os.path.join(os.path.dirname(__file__), "..", "third_party", "Lux-Design-S2", "kits", "python")


def load_starter_bot():
    # The kit imports its own `agent` and `lux` modules, so its folder has to be on the path.
    sys.path.insert(0, os.path.abspath(KIT_DIR))
    spec = importlib.util.spec_from_file_location("starter_bot_main", os.path.join(KIT_DIR, "main.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # agent_fn keeps one Agent per player name, so one module can play both sides.
    return module.agent_fn


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--delay", type=float, default=0.05, help="seconds to wait after each turn")
    args = parser.parse_args()

    agent_fn = load_starter_bot()
    env = LuxAI_S2(render_mode="human")
    env.reset(seed=args.seed)
    state_obs = env.state.get_compressed_obs()
    obs = to_json(state_obs)
    env_cfg = dataclasses.asdict(env.state.env_cfg)
    env.render()

    while True:
        actions = {}
        for player in env.agents:
            observation = Namespace(step=env.env_steps, obs=json.dumps(obs), remainingOverageTime=60, player=player)
            # from_dict mutates its argument, so each call gets its own copy.
            action = agent_fn(observation, dict(env_cfg=json.loads(json.dumps(env_cfg))))
            actions[player] = {k: np.array(v) if isinstance(v, list) else v for k, v in action.items()}

        new_obs, rewards, terminations, truncations, _ = env.step(actions)
        obs = to_json(env.state.get_change_obs(state_obs))
        state_obs = new_obs["player_0"]

        env.render()
        if any(event.type == pygame.QUIT for event in pygame.event.get()):
            break
        time.sleep(args.delay)

        if all(terminations[p] or truncations[p] for p in terminations):
            lichen = {p: int(r) for p, r in rewards.items()}
            print(f"Game over at turn {env.state.real_env_steps}. Lichen: {lichen}")
            # Leave the final board on screen until the window is closed.
            while not any(event.type == pygame.QUIT for event in pygame.event.get()):
                time.sleep(0.1)
            break

    pygame.quit()


if __name__ == "__main__":
    main()
