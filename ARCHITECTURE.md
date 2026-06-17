# Architecture

AI Arena is a PvPvP tournament harness: three LLM competitors (Codex, Opus,
Gemini) each design a 2-player turn-based game ("home field") and field an
agent that plays on its own game, on rivals' games, and on a neutral baseline.

Three design rules shape everything below:

1. **Everything on the wire is JSON.** Game states and moves are plain
   JSON-serializable values, so games and agents can be replayed from logs,
   driven over a subprocess pipe, or written in another language.
2. **The harness never trusts a game or an agent.** Games and agents live in
   per-model folders *outside* the installed package and are loaded
   dynamically; the engine treats exceptions, timeouts, and illegal moves as
   forfeits rather than crashes.
3. **Run artifacts are durable, and durability is never load-bearing.**
   Matches can be hours of LLM calls, so match logs and tournament results
   are written atomically (unique temp file, fsynced, renamed into place)
   and incrementally as the run progresses — a crash or Ctrl-C keeps
   everything up to the last snapshot, and even power loss leaves a valid
   recent snapshot rather than a torn file. Progress writes are best-effort:
   a failed snapshot warns and plays on, because the protection must never
   abort the match it protects.

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
  ("no_legal_moves"). `MatchResult.turns` counts successfully applied moves
  on every path; a forfeited attempt is recorded in `move_history` (with a
  `note`) but not counted. Records per-move timing and can write a full JSON
  match log (`result` + `final_state` + `final_render`). Per rule 3 the log
  is snapshotted as moves are applied with a stub result
  (`reason: "in_progress"`), throttled to ~1/s so fast agents don't make the
  rewrite O(n²); the final write records the real result, and partial logs
  replay normally (replay falls back to the engine-recorded `in_progress`
  terminal). `prime_pause` implements the arena rule granting an extra
  analysis cycle after prime-numbered turns.
- `loading.py` — `load_symbol("<path>:<symbol>")`, the dynamic-import
  mechanism the CLI/tournament use to reach games and agents in model
  folders.
- `agents/` — built-ins: `human` (stdin), `random`, `greedy`, `search`, `mcts`,
  and `SubprocessAgent`. `greedy` is a game-agnostic baseline (in `greedy.py`) that
  uses only the `Game` protocol — it grabs an immediate win, else avoids moves
  that let the opponent win or that lose on the spot, else plays randomly;
  shallow (1 ply each way) but a real skill floor above `random`. `search`
  (in `search.py`) is the next rung: a game-agnostic depth-limited negamax with
  alpha-beta, scoring leaves by terminal outcome only (win/loss/draw within the
  horizon). It plays perfectly on games small enough to search to the end (it
  never loses tic-tac-toe) and behaves like a deeper-horizon `greedy` on the
  larger arena games; a per-turn `node_budget` (default 40k) bounds its cost on
  high-branching games, like `greedy`'s `safety_budget`. `mcts` (in `mcts.py`)
  is the strongest baseline: a game-agnostic Monte Carlo Tree Search (UCT
  selection, random rollouts, most-visited root move). Because it estimates
  positions from random playouts rather than a fixed horizon, it keeps improving
  on the big arena games where `search` has no heuristic — at full budget it
  outplays `greedy` on the larger arena games (a clean sweep on Caldera, a clear
  edge on Skysummit) — yet still draws solved games like tic-tac-toe. Its
  strength tracks how informative random play is:
  on Photon, where random games almost always reach the turn-cap draw, the
  rollout signal is weak and it plays no better than `random`. Bounded per turn
  by `iterations` (default 800) and a `node_budget` (default 50k) on apply_move
  calls. `random`, `greedy`, `search`, and `mcts` take an optional `seed` for
  reproducible play (`ai-arena play --seed`). `SubprocessAgent` speaks the JSONL protocol (`docs/protocol.md`) to a
  long-running bot process with a per-turn timeout. The bot's stderr is drained
  on a background thread (a chatty bot cannot deadlock the match by filling the
  pipe buffer) and its tail is attached to the error when the bot dies.
- `tournament.py` — round-robin from a TOML config (`arena.toml`). Each
  pairing plays three contexts: both competitors' home games plus the third
  competitor's home game (falls back to `neutral_game` when there is no
  third). Supports multiple rounds and `swap_starts`. Scoring: win 3 / draw
  1 / loss 0. Emits per-match logs and a JSON results file (`results.json`),
  the latter rewritten per rule 3 before the first match (fencing out stale
  data from a previous run with the same `--out`) and after every match;
  `complete: false` until the run finishes.
  Specs are resolved up front (config typos fail fast), but per-match crashes
  are contained so one bug cannot lose a whole expensive run: an agent that
  fails to start forfeits its match (`agent_spawn_failed:...`), and a crash
  in game code voids the match (`match_error:...`, recorded but no points).
- `benchmark.py` — `run_benchmark`: plays N independent matches between two
  agents and reports outcomes *by contestant* (not by seat). Seats alternate
  by default (`swap_starts`) so first-mover advantage cancels out, and a
  `base_seed` gives every game distinct, reproducible seeds (game `i` seeds the
  two seats `base_seed + 2i` / `+ 2i + 1`). It builds a fresh agent per game so
  games are independent and stateful bots are not replayed; outcomes are
  attributed to the named contestant regardless of which seat it took that
  game. Reports win/draw counts, a forfeit tally (timeout / agent_error /
  illegal_move) split by contestant, a terminal-reason histogram (so you can
  see *how* games end — `crown_captured`, `reach_level3`, …), average turns,
  and per-contestant think-time (avg/max). A `KeyboardInterrupt` returns the
  partial result (`incomplete=True`) so a long run can be stopped without
  losing what it found. `human` is rejected (it blocks on stdin).
- `cli.py` — `ai-arena list-games | list-agents | play | benchmark | gui |
  tournament`.
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

- `tests/` — pytest suite for the engine, replay, subprocess agent,
  tournament crash containment, the `benchmark` command (outcome attribution
  by contestant, seat-swap balancing, forfeit attribution, seeded
  reproducibility, partial-on-interrupt), GUI log-name inference, the built-in
  baseline agents (`greedy`, `search`, `mcts` — tactics, strong/optimal
  tic-tac-toe play, legality/determinism/budget invariants, robustness on
  misbehaving games), and all three home games (move generation, rules edge
  cases, turn-limit pins).
  `gemini/game/test_game.py` holds additional Photon tests that run from the
  repo root as well.
- `.github/workflows/` — CI runs `pytest -q` on Python 3.12 for pushes to
  `main` and PRs.
