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
- `models/codex/`: Codex game + agent scaffolding
- `models/opus/`: Opus game + agent scaffolding
- `models/gemini/`: Gemini game + agent scaffolding
- `docs/`: rules + protocol

## Quick Start (Local)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

ai-arena list-games
ai-arena play tictactoe --p0 human --p1 random
ai-arena tournament --config arena.toml
```

## Add Your Game / Agent

Drop a Python game/agent into a model folder and point `ai-arena` at it:

```bash
ai-arena play /abs/path/to/game.py:MyGame \
  --p0 /abs/path/to/agent0.py:MyAgent \
  --p1 random
```

For cross-language agents, use the JSONL subprocess protocol in `docs/protocol.md`.
