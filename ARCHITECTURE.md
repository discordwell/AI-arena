# Architecture

AI Arena is a PvPvP tournament harness: three LLM competitors (Codex, Opus,
Gemini) each design a 2-player turn-based game ("home field") and field an
agent that plays on its own game, on rivals' games, and on a neutral baseline.

Two design rules shape everything below:

1. **Everything on the wire is JSON.** Game states and moves are plain
   JSON-serializable values, so games and agents can be replayed from logs,
   driven over a subprocess pipe, or written in another language.
2. **The harness never trusts a game or an agent.** Games and agents live in
   per-model folders *outside* the installed package and are loaded
   dynamically; the engine treats exceptions, timeouts, and illegal moves as
   forfeits rather than crashes.

## Control Harness (`src/ai_arena/`, installed as `ai-arena`)

- `game.py` — the `Game` protocol every game implements:
  `initial_state`, `legal_moves(state, player)`, `apply_move(state, player,
  move)`, `terminal(state) -> Terminal`, `render(state)`. Players are `0`
  and `1`; `Terminal` carries `is_terminal` / `winner` (`None` = draw) /
  `reason`.
- `engine.py` — `play_match(game, agent0, agent1, ...)`: alternating turns;
  checks `terminal` before each move; forfeits the mover on `TimeoutError`
  ("timeout"), any other exception ("agent_error"), a move not present in
  `legal_moves` ("illegal_move"), or an empty legal-move list
  ("no_legal_moves"). Records per-move timing and can write a full JSON match
  log (`result` + `final_state` + `final_render`). `prime_pause` implements
  the arena rule granting an extra analysis cycle after prime-numbered turns.
- `loading.py` — `load_symbol("<path>:<symbol>")`, the dynamic-import
  mechanism the CLI/tournament use to reach games and agents in model
  folders.
- `agents/` — built-ins: `human` (stdin), `random`, and `SubprocessAgent`,
  which speaks the JSONL protocol (`docs/protocol.md`) to a long-running bot
  process with a per-turn timeout.
- `tournament.py` — round-robin from a TOML config (`arena.toml`). Each
  pairing plays three contexts: both competitors' home games plus the third
  competitor's home game (falls back to `neutral_game` when there is no
  third). Supports multiple rounds and `swap_starts`. Scoring: win 3 / draw
  1 / loss 0. Emits per-match logs and a JSON results file (`results.json`).
- `cli.py` — `ai-arena list-games | play | gui | tournament`.
- `gui.py` — generic Tkinter board GUI for live matches and log replay.
- `replay.py` — rebuilds the state sequence from a match log's move history;
  falls back to the engine's recorded result for forfeit endings that game
  rules alone cannot detect.
- `games/tictactoe.py` — built-in neutral game.

## Model Folders (`codex/`, `opus/`, `gemini/`)

Each competitor has:

- `<model>/game/` — the home game plus its rules doc (the doc is part of the
  competition: rival agents read it to learn the game):
  - Codex: **Skysummit** (`game.py:CodexGame`, rules in `GAME.md`) — 5x5
    move-and-build climbing duel.
  - Opus: **Caldera** (`game.py:OpusGame`, rules in `rules.md`) — 7x7
    volcanic tactics with chain-reaction eruptions.
  - Gemini: **Photon** (`game.py:GeminiGame`, rules in `rules.md`) — 10x10
    laser tactics. Also ships a standalone pygame GUI/play harness
    (`gui.py`, `play.py`, `main.py`) that predates the shared Tkinter GUI.
- `<model>/agent/` — the tournament agent (`agent.py:<Model>Agent`), a thin
  wrapper that launches `<model>_subprocess_bot.py` via `SubprocessAgent`.
  The bot calls the model's API and replies with a move. Env overrides:
  `<MODEL>_ARENA_MODEL` (model id, where supported) and
  `<MODEL>_ARENA_COMMAND` (replace the whole bot command).

Turn limits are deliberately reduced for tournament speed (Skysummit and
Caldera end at 50 plies; Photon is drawn once more than 30 moves are played).
The rules docs state the reduced caps and `tests/` pins them.

## Configuration & Docs

- `arena.toml` — competitors (id, home game spec, agent spec), neutral game,
  rounds, `swap_starts`, `prime_pause`, optional `log_dir`.
- Specs are either built-in names (`tictactoe`), `"<path>:<symbol>"`, or for
  agents `subprocess:<command...>`.
- `docs/rules.md` — arena format and scoring; `docs/protocol.md` — game/agent
  interfaces and the JSONL subprocess protocol.

## Testing & CI

- `tests/` — pytest suite for the engine, replay, subprocess agent, and all
  three home games (move generation, rules edge cases, turn-limit pins).
  `gemini/game/test_game.py` holds additional Photon tests that run from the
  repo root as well.
- `.github/workflows/` — CI runs `pytest -q` on Python 3.12 for pushes to
  `main` and PRs.
