# AI Arena

PvPvP: Codex vs Opus vs Gemini.

Each model designs a 2-player turn-based game (their "home field"), then plays:

1. On its own game (home).
2. On rivals' games (away).
3. On a neutral game (common baseline).

Scoring:

- Subjective: how fun/interesting the game is to watch and play.
- Objective: performance in matches (wins/draws + timeouts/illegal moves).

This repo contains a small, language-agnostic control harness plus per-model
folders to drop in games/agents.

## Repo Layout

- `src/ai_arena/`: control harness (engine + CLI + protocol)
- `codex/`: Codex game + agent scaffolding
- `opus/`: Opus game + agent scaffolding
- `gemini/`: Gemini game + agent scaffolding
- `docs/`: rules + protocol

## Quick Start (Local)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

ai-arena list-games
ai-arena list-agents
ai-arena play tictactoe --p0 human --p1 random

# Built-in baselines: `random`, `greedy` (a game-agnostic 1-ply win/loss-aware
# player), `search` (depth-limited alpha-beta negamax; plays tic-tac-toe
# perfectly), and `mcts` (game-agnostic Monte Carlo Tree Search; the strongest
# baseline on the larger arena games, where `search` has no heuristic past its
# horizon). `--seed` makes a match with built-in agents reproducible.
ai-arena play tictactoe --p0 greedy --p1 random --seed 1
ai-arena play tictactoe --p0 search --p1 greedy --seed 1
ai-arena play codex/game/game.py:CodexGame --p0 mcts --p1 greedy --seed 1

# Dial a baseline's strength with `name:knob=value[,knob=value]` (anywhere an
# agent spec is accepted). Bare names keep their defaults; tunable knobs are
# `greedy:safety_budget`, `search:max_depth`/`node_budget`, and
# `mcts:iterations`/`node_budget`/`rollout_depth`/`exploration`.
ai-arena play tictactoe --p0 "search:max_depth=2" --p1 random --seed 1
ai-arena benchmark tictactoe --p0 "mcts:iterations=2000" --p1 mcts --games 20 --seed 1

# Benchmark one agent against another over many games (seats alternate so
# first-mover advantage cancels out) and report head-to-head win/draw rates.
ai-arena benchmark tictactoe --p0 search --p1 random --games 100 --seed 1

# Round-robin: rank two or more agents on one game into a leaderboard (every
# pairing plays a seat-balanced benchmark; win 3 / draw 1 / loss 0, like the
# tournament). The N-way generalization of `benchmark`.
ai-arena round-robin tictactoe --agents random greedy search mcts --games 20 --seed 1

ai-arena tournament --config arena.toml --out results.json

# Read a tournament results file back into a ranked leaderboard + head-to-head
# record (headless; the post-hoc reader for tournament artifacts).
ai-arena standings results.json
ai-arena standings results.json --by-context   # also: each player's home/away record
ai-arena standings results.json --matches      # also: list every recorded match

# Replay a saved match log to the terminal (headless; no GUI/Tkinter needed).
# The game is inferred from the log when possible (--game to override/supply it).
ai-arena play tictactoe --p0 search --p1 random --seed 7 --log /tmp/m.json
ai-arena replay /tmp/m.json            # summary + final board
ai-arena replay /tmp/m.json --moves    # also list the move history
ai-arena replay /tmp/m.json --frames   # render the board at every turn

# GUI (optional)
ai-arena gui --game codex/game/game.py:CodexGame --p0 human --p1 random
ai-arena gui --load-log /abs/path/to/match_log.json
```

## Add Your Game / Agent

Drop a Python game/agent into a model folder and point `ai-arena` at it:

```bash
ai-arena play /abs/path/to/game.py:MyGame \
  --p0 /abs/path/to/agent0.py:MyAgent \
  --p1 random
```

Before entering a new game in an (expensive) tournament, pre-flight it with
`check-game`: a game-agnostic conformance check that the game implements the
`Game` protocol the engine, replay, and baseline agents rely on. It verifies the
state and moves are JSON round-trippable, that `apply_move` and the read-only
methods never mutate their input state, that `legal_moves` and `apply_move`
agree, that `terminal` returns a well-formed verdict, and (via seeded random
self-play) that the game actually terminates. Exit code is 0 on pass, 1 on a
protocol violation, so it drops into CI.

```bash
ai-arena check-game tictactoe
ai-arena check-game /abs/path/to/game.py:MyGame --seed 1
ai-arena check-game opus/game/game.py:OpusGame --playouts 50
```

For cross-language agents, use the JSONL subprocess protocol in `docs/protocol.md`.
