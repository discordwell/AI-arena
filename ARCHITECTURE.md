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
  ("no_legal_moves"). When the `max_turns` cap binds it re-checks `terminal`
  on the state the final permitted move produced before declaring the cutoff,
  so a decisive last move (a win, or a rules-draw such as a filled board)
  landing exactly on the cap is scored as that real outcome rather than a bogus
  `max_turns` draw — matching the live GUI, which already checked terminal
  before its own `max_turns` override. `MatchResult.turns` counts successfully
  applied moves on every path (so `max_turns` on the cap); a forfeited attempt
  is recorded in `move_history` (with a `note`) but not counted. Records per-move timing and can write a full JSON
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
- `specs.py` — the single source of truth for turning a user-written *spec*
  (a game name / `<path>:<symbol>`, or an agent `name[:knob=val]` /
  `subprocess:<cmd>` / `<path>:<symbol>`) into a factory: `resolve_game_factory`,
  `resolve_agent_factory`, `parse_subprocess_command`, and the `BUILTIN_GAMES`
  registry. Every entry point (`cli` / `gui` / `benchmark` / `round-robin` /
  `tournament`) used to re-implement this dispatch inline, so the subprocess
  parsing (with its error message), the built-in fall-through, and the
  `<path>:<symbol>` loading were each copied four times and free to drift; they
  now all route through here, the same treatment `agents/builtins.py` gives the
  tunable-agent grammar. `resolve_agent_factory` returns a *seed-aware* factory
  `make(seed) -> agent` so one resolver serves two seeding policies: `play
  --seed` / `benchmark` thread a per-game seed into the seedable built-ins for
  reproducibility, while `tournament` / `gui` pass `None` to leave them
  deliberately unseeded. `human` is resolved by each caller (its class differs —
  stdin vs GUI — and some entry points reject it). `tests/test_specs.py` pins the
  resolver and guards that all four entry points still agree.
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
  tunable params (type + lower bound). The entry-point loaders route built-in
  names through it (via `specs.py`), so the parsing/validation lives in one
  tested place and a bare name still builds exactly today's default agent
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
- `check.py` — `check_game` + the `check-game` command: a game-agnostic
  protocol-conformance pre-flight. The harness never trusts a game *at runtime*
  (it forfeits on any exception); this finds a broken game *before* an expensive
  run instead. Using only the Game protocol it runs `check_game(game)` — a
  battery returning pass/warn/fail `CheckResult`s plus an ending histogram — over
  the opening position and `playouts` seeded random self-play games. It enforces
  the contracts the rest of the harness assumes: state and moves are strict-JSON
  and survive a round-trip (design rule #1; tuples/non-str keys are caught),
  `apply_move` and the read-only methods (`legal_moves`/`terminal`/`render`)
  never mutate their input (the contract every baseline agent + replay rely on,
  and the class of the old `Photon legal_moves` bug), `legal_moves`/`apply_move`
  agree, `terminal` returns a well-formed verdict (winner ∈ {None,0,1}), and the
  game terminates under random play. The playout loop mirrors the engine's turn
  structure, including the same max_turns-boundary handling: a game that becomes
  terminal on the move played at the cap is counted as its real ending, not as a
  spurious `max_turns` non-termination. Like the agents it never raises on a hostile
  game — every game call (and attribute read) is wrapped, becoming a fail. Reuses
  `tournament._game_factory` to load the spec; CLI exit is 0 pass / 1 fail / 2
  unloadable-spec.
  Also owns `check_agent` + the `check-agent` command: the same pre-flight for
  the other untrusted half of a match. Agent failures are the *expensive* ones —
  a bot that fails to spawn forfeits its tournament matches
  (`agent_spawn_failed:`), and one that times out, crashes, or moves illegally
  forfeits mid-run — and two defects are invisible even then: the engine hands
  agents the live state object and legal-move list and validates the returned
  move against that same list, so a state-mutating agent silently corrupts the
  match and a legal-list-mutating one can bypass illegal-move detection
  entirely. `check_agent(agent_factory, game_factory)` constructs a fresh agent
  per game exactly as `benchmark` would (reusing `_seeded_agent_factory`; spawn
  failures and startup stderr surface in the report) and plays seeded,
  seat-alternating instrumented games against a seeded `random` opponent,
  checking construction, `name`/`select_move` shape, move legality (against a
  pre-call snapshot, judged exactly as the engine does, with a hint for the
  classic tuple-vs-list mistake), no escaping exception/timeout, and
  state/legal-list purity via pre/post fingerprints (in-place reordering is a
  WARN — harmless today, but engine-owned data; a set change is a FAIL). The
  game is presumed conforming (run `check-game` first): a game-side error is a
  WARN pointing there, never blamed on the agent, and a run that observed zero
  agent moves FAILs coverage rather than passing vacuously. Games default to 2
  (one per seat) because each agent move may be a paid LLM call, and the first
  agent-failed game stops the run. Reports an agent-perspective ending histogram
  plus think-time (avg/max ms); `human` is rejected (blocks on stdin); same exit
  codes as `check-game`.
- `cli.py` — `ai-arena list-games | list-agents | play | replay | check-game |
  check-agent | benchmark | round-robin | gui | tournament | standings`. `replay` reads a durable match log back to the terminal
  with no GUI/Tkinter dependency (summary + final board, optional `--moves`
  and per-frame `--frames`): it reconstructs and re-validates the match via
  `replay.py` when the game is loadable (inferred from the log, or `--game`),
  and otherwise falls back to the result/`final_render` stored in the log so a
  log of a game absent from this repo still summarizes from JSON alone.
  `main()` is also the CLI's error boundary: an exception escaping a
  subcommand is by construction a setup problem — a bad spec, param, or
  config (runtime game/agent failures are contained per-match by the engine,
  tournament, and benchmark) — so it prints a one-line `error: <Type>: <msg>`
  to stderr and exits 2, matching the bad-spec exit code the check-* commands
  already used, instead of dumping a traceback (`AI_ARENA_DEBUG=1` restores
  the traceback). An unhandled Ctrl-C exits 130 (`interrupted`; benchmark and
  round-robin still contain their own interrupts and report partial results),
  and a broken stdout pipe (`... | head`) exits 141 silently.
- `gui.py` — generic Tkinter board GUI for live matches and log replay.
- `replay.py` — rebuilds the state sequence from a match log's move history,
  re-validating each applied move against the live game rules as it goes (a
  logged move not in `legal_moves` for the reconstructed state means the log is
  corrupt/tampered or from a different game version; the strict games raise from
  `apply_move` anyway, but this makes the "re-validated" contract hold for a
  *lenient* game too, which would otherwise apply a bad move as a silent no-op
  and reconstruct a wrong state — the `replay` command and GUI catch it and fall
  back to the stored result). Falls back to the engine's recorded result for
  forfeit endings that game rules alone cannot detect. Also owns
  `infer_game_spec_from_log` (mapping a log's stored `game` name back to a
  loadable spec), shared by the `replay` command and the GUI so neither path
  needs to hardcode game locations twice.
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
  that a bad parameter fails fast, the shared spec resolver (`test_specs.py`:
  game/agent/subprocess/path resolution, the two seeding policies, and a drift
  guard that all four entry-point loaders resolve a spec identically), the
  `check-game` conformance checker
  (`test_check.py`: every home game conforms; a hand-built broken game fails
  exactly its target check — mutating `legal_moves`/`apply_move`, non-JSON or
  non-round-trippable state, legal/apply disagreement, malformed/bool/raising
  `terminal`, non-terminating play, dead-on-arrival; the checker never raises on
  a hostile game incl. a raising `name` or `Terminal` attribute; determinism;
  formatter; CLI exit codes), the `check-agent` conformance checker
  (`test_check_agent.py`: built-ins pass; a broken agent fails exactly its
  target check — illegal move (with the tuple hint), raising/timing-out
  `select_move`, live-state mutation, legal-list content mutation (the defect
  the engine's own validation cannot see), construction failure, missing
  `select_move`, bad `name`; in-place reordering is a WARN not a FAIL; hostile
  attributes never crash the checker; a first-turn agent failure is not doubly
  reported as a coverage failure; a misbehaving game is a WARN pointing at
  check-game plus a coverage FAIL, never blamed on the agent; fresh agent per
  game, each closed; failure stops scheduling; seat alternation; seeded
  determinism; formatter; CLI exit codes incl. spawn-failure = FAIL verdict and
  `human`/bad-spec/bad-game = exit 2), and all three home games (move
  generation, rules edge cases, turn-limit pins).
  `gemini/game/test_game.py` holds additional Photon tests that run from the
  repo root as well (laser-trace coordinates are JSON lists so the state survives
  a round-trip — a latent bug `check-game` surfaced and this change fixed).
- `.github/workflows/` — CI runs `pytest -q` on Python 3.12 for pushes to
  `main` and PRs.
