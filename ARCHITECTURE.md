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
- `agents/builtins.py` — `resolve_builtin_agent`, the single source of truth for
  the four seedable built-ins and the tunable-strength spec syntax
  `name:knob=value[,knob=value]` (e.g. `search:max_depth=6`,
  `mcts:iterations=2000,exploration=1.0`). It returns `(AgentClass, kwargs)` for
  a built-in name (the caller adds its own `seed`) or `None` so the caller falls
  through to its `human` / `subprocess:` / `<path>:<symbol>` handling, and raises
  a clear `ValueError` on an unknown knob, a malformed `key=value`, a non-numeric
  value, or an out-of-range one. A `BUILTIN_AGENTS` registry pins each agent's
  tunable params (type + lower bound). The CLI, tournament, benchmark, and GUI
  loaders all route built-in names through it, so the parsing/validation lives in
  one tested place and a bare name still builds exactly today's default agent
  (the feature is additive). The arena tools (`benchmark` / `round-robin` /
  `tournament`) resolve specs up front, so a bad parameter fails fast before any
  match runs.
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
  Also owns the `standings` report (`compute_standings` / `format_standings` /
  `parse_match_summaries`, driven by `cmd_standings`): the headless, post-hoc
  reader for a results file, mirroring what `replay` is for match logs. It
  recomputes the leaderboard from the recorded `matches` using the same scoring
  rule as the live run (`_apply_result`) — so it is self-consistent and works on
  partial or older files (it tolerates a missing `complete`/`scoreboard`) — and
  adds a head-to-head record, a per-competitor home/away split, and a
  termination-reason histogram. A `match_error:` match is excluded from scoring
  (a void is not a draw), matching the live tournament.
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
  Also owns `round-robin` (`run_round_robin` / `compute_round_robin_standings` /
  `format_round_robin`, driven by `cmd_round_robin`): the N-way generalization of
  `benchmark`. It plays a full `run_benchmark` for every unordered pairing of two
  or more agents on one game and aggregates the games into a points leaderboard
  (win 3 / draw 1 / loss 0 — the same rule as the tournament) plus a per-pairing
  head-to-head record. Each pairing inherits `run_benchmark`'s seat-swap
  balancing and per-game seeding, and gets a disjoint seed window (pairing `k`
  starts at `base_seed + k·2·games`) so pairings are independent yet the whole
  round-robin replays identically. Crash containment is inherited too: a pairing
  interrupted or failed mid-run keeps its completed games, marks the round-robin
  `incomplete`, and stops scheduling further pairings (exit 130, like
  `benchmark`). Agent specs must be distinct, and `human` is rejected.
- `cli.py` — `ai-arena list-games | list-agents | play | replay | benchmark |
  round-robin | gui | tournament | standings`. `replay` reads a durable match log back to the terminal
  with no GUI/Tkinter dependency (summary + final board, optional `--moves`
  and per-frame `--frames`): it reconstructs and re-validates the match via
  `replay.py` when the game is loadable (inferred from the log, or `--game`),
  and otherwise falls back to the result/`final_render` stored in the log so a
  log of a game absent from this repo still summarizes from JSON alone.
- `gui.py` — generic Tkinter board GUI for live matches and log replay.
- `replay.py` — rebuilds the state sequence from a match log's move history;
  falls back to the engine's recorded result for forfeit endings that game
  rules alone cannot detect. Also owns `infer_game_spec_from_log` (mapping a
  log's stored `game` name back to a loadable spec), shared by the `replay`
  command and the GUI so neither path needs to hardcode game locations twice.
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
  tournament crash containment, the `standings` report (point scoring + ranking,
  head-to-head, home/away split, void exclusion, defensive parsing, and an
  invariant that the recomputed scoreboard matches the live tournament's), the
  `benchmark` command (outcome attribution
  by contestant, seat-swap balancing, forfeit attribution, seeded
  reproducibility, partial-on-interrupt), the `round-robin` command (points/record
  aggregation and ranking, label tie-break, forfeit attribution, per-pairing
  disjoint seed windows, distinct-agent + positive-games guards, and keep-completed
  on a mid-run pairing failure), the `replay` command (log round-trip
  and rendering, fallback to stored data when the game is unknown, explicit
  `--game`, clean error on a bad path) and the game-name inferrer it shares
  with the GUI, the built-in
  baseline agents (`greedy`, `search`, `mcts` — tactics, strong/optimal
  tic-tac-toe play, legality/determinism/budget invariants, robustness on
  misbehaving games), the tunable-agent resolver (`test_builtins.py`: bare-name
  defaults, int/float param coercion, partial params, non-builtin fall-through,
  every malformed/unknown/out-of-range param error, and a registry-vs-CLI drift
  guard), plus end-to-end checks that a tuned spec actually changes strength and
  that a bad parameter fails fast, and all three home games (move generation,
  rules edge cases, turn-limit pins).
  `gemini/game/test_game.py` holds additional Photon tests that run from the
  repo root as well.
- `.github/workflows/` — CI runs `pytest -q` on Python 3.12 for pushes to
  `main` and PRs.
