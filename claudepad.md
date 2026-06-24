# Claudepad

## Session Summaries

### 2026-06-23 ~UTC — Add `ai-arena round-robin` command (N-way agent leaderboard on one game)
- New `ai-arena round-robin <game> --agents SPEC [SPEC ...] [--games N] [--seed S]
  [--no-swap] [--out f] [--quiet]` (`src/ai_arena/benchmark.py`:
  `compute_round_robin_standings` / `run_round_robin` / `format_round_robin` pure-ish
  functions + `cmd_round_robin` / `load_round_robin_parser`; registered in `cli.py`).
  The N-way generalization of `benchmark`: it plays a full `run_benchmark` for every
  unordered pairing and aggregates the games into a points leaderboard
  (win 3 / draw 1 / loss 0 — the SAME rule as the tournament's `_apply_result`) plus a
  per-pairing head-to-head. Closes the gap between `benchmark` (exactly 2 agents) and
  `tournament` (fixed config-driven 3-way PvPvP across several games): neither could
  "rank these N agents on this one game."
- Built entirely on the tested `run_benchmark`, so each pairing inherits seat-swap
  balancing + per-game seeding + crash containment. Each pairing gets a DISJOINT seed
  window (pairing k starts at `base_seed + k*2*games`; run_benchmark uses
  `base+2i`/`+2i+1` per game, so windows of width 2*games never overlap) → independent
  pairings, fully reproducible round-robin. Incomplete pairing (Ctrl-C / spawn-fail /
  game crash) keeps its completed games, marks the run incomplete, stops scheduling
  further pairings, exit 130 (mirrors benchmark). Distinct specs required; `human`
  rejected (blocks on stdin). `--out` writes JSON atomically (asdict over nested
  BenchmarkResults + computed standings).
- Wet-tested: 4 baselines on tictactoe (mcts==search top @68pts both never-lose,
  greedy mid, random last; search-vs-mcts 12/12 draws); real Caldera game
  (greedy>search>random); reproducible (same seed → identical); `--out` JSON round-trip,
  no leftover .tmp; path-spec + forfeiting agent (12 forfeits, ranked last, forfeit
  section renders) + `--no-swap`; bad path agent fails fast at spec-resolution (like
  benchmark); CLI exit-130 end-to-end (flaky agent fails mid-pairing → INTERRUPTED
  header + per-pairing [INTERRUPTED] + 2 completed games kept + rc 130).
- Code review (adversarial subagent): NO correctness bugs. Confirmed seed-window
  disjointness, points/record aggregation (A's win == B's loss, draws on both,
  points==3*wins+draws even on partial pairings), crash-containment halt, all guards.
  3 nits, all intentional convention-matches (factory-resolve order mirrors
  cmd_benchmark; W-D-L columns match the dominant codebase convention; defensive
  label guard is API-path defense-in-depth).
- Tests: tests/test_round_robin.py (18: pure scoring+records+tie-break+forfeits+
  unknown-label-skip, integration ranking on tictactoe, reproducibility, per-pairing
  disjoint-seed-window proof vs standalone benchmark, <2-agents/duplicate/zero-games
  guards, keep-completed-on-mid-run-failure, formatting incl. incomplete flag, 5 CLI).
  174 → 192 tests, suite ~8s → ~9.3s. Docs: README quick-start, ARCHITECTURE
  (benchmark.py + cli command list + testing sections).

### 2026-06-18 ~UTC — Add headless `ai-arena standings` command (read tournament results.json back)
- New `ai-arena standings <results.json> [--by-context] [--matches]`
  (`src/ai_arena/tournament.py`: `parse_match_summaries` / `compute_standings` /
  `format_standings` pure functions + `cmd_standings` / `load_standings_parser`;
  registered in `cli.py`). Closes the exact gap `replay` closed for match logs:
  a tournament `results.json` is durable (rule 3) but could previously only be
  understood from the live stdout scoreboard at run time — useless after the run
  or on a headless box. This is the post-hoc reader for tournament artifacts.
- Report: ranked leaderboard (pts/W/L/D/played), head-to-head (W-D-L per pairing,
  tallied from the smaller id's view), termination-reason histogram; `--by-context`
  adds each player's home/away record, `--matches` lists every match. Derived from
  the recorded `matches` using the SAME scoring rule as the live run
  (reuses `_apply_result`), so it's self-consistent and works on partial/older
  files (tolerates a missing `complete`/`scoreboard`). A `match_error:` match is
  voided (no points, NOT a draw), matching the live `outcome.scored` gate.
- KEY VALIDATION: recomputing the checked-in `results.json` reproduces its stored
  scoreboard exactly (gemini 13 / codex 8 / opus 4). The report also surfaces real
  insight that was invisible before — gemini went 4-0-0 away but 0-1-1 at home;
  opus went 0-0-2 at home. Pinned as an invariant test (recomputed == live
  `res.scoreboard`).
- Wet-tested: real results.json (default/--by-context/--matches), a fresh
  `tournament --out` round-trip (complete=true), voided+incomplete file
  (`[INCOMPLETE run]`, VOID row), missing file / non-object JSON / malformed JSON
  → clean rc 1, empty matches → "(no matches recorded)".
- Multi-agent code review found one real [RISK]: a corrupt/hand-edited `winner`
  that is neither contestant would KeyError in the reused `_apply_result` (live
  run is immune — it builds winner from seat ids). Fixed in the parser: coerce
  `winner` to None unless it equals p0/p1 (regression-tested). Core scoring
  invariant + h2h perspective + home/away + defensive parsing all verified clean.
- Tests: tests/test_standings.py (10: scoring+ranking, head-to-head, home/away
  split, void exclusion, defensive+bogus-winner parsing, incomplete/empty
  formatting, section/optional-block gating, live-scoreboard invariant) +
  tests/test_cli.py (+3: tournament→standings round-trip, missing-file + non-object
  errors). 162 → 174 tests. Docs: README quick-start, ARCHITECTURE (cli + tournament
  + testing sections).

### 2026-06-17 ~UTC — Add headless `ai-arena replay` command (read match logs without the GUI)
- New `ai-arena replay <log> [--game <spec>] [--moves] [--frames]`
  (`src/ai_arena/cli.py`: `cmd_replay`, pure formatter `_format_replay`). Closes
  a real gap: the durable, crash-safe match logs (rule 3) could previously only
  be read back through the Tkinter GUI (`gui --load-log`) — useless on a
  headless tournament box. The whole point of the durability work is logs you
  can read later; this is the headless reader.
- Behavior: when the game is loadable (inferred from the log's `game` name, or
  `--game`), it reconstructs + re-validates the match via the tested `replay.py`
  and prints `game (replayed)`, winner/reason/turns, and the final board;
  `--moves` lists move history, `--frames` renders every frame. When the game is
  NOT loadable (unknown name / game absent from this repo) it falls back to the
  result + `final_render` stored in the log, so it still summarizes from JSON
  alone — just degraded (a stderr warning; `--frames` reports unavailable).
  Clean exit 1 (not a traceback) on a missing/unreadable/non-JSON log.
- Refactor: moved `_repo_root` + `_infer_game_spec_from_log` out of `gui.py`
  (Tkinter-only) into `replay.py` as public `infer_game_spec_from_log`; `gui.py`
  re-exports it under the old private name (so `test_gui_infer.py` is unchanged).
  Side benefit: the moved version guards `result` not being a dict, fixing a
  latent `AttributeError` the old `payload.get("result", {}).get(...)` had.
- Wet-tested: tictactoe (replayed + moves + frames), Caldera inferred from log
  name, winner=0 / turns=0 print correctly (no falsy-zero bug), unknown-game
  fallback prints stored render + warns, missing file → rc 1.
- Tests: tests/test_cli.py (+5: round-trip+render, moves/frames count, unknown-
  game fallback, explicit `--game`, missing-file error) and tests/test_replay.py
  (+1: canonical inferrer incl. malformed-payload robustness). 156 → 162 tests.
- Multi-agent code review (correctness + removed-behavior + reuse/conventions):
  no correctness bugs; behavior of the moved inferrer exactly preserved; applied
  one simplification (bind the `replayed` flag once instead of repeating the
  `rep/game is not None` test 3×). Docs: README quick-start, ARCHITECTURE (cli +
  replay.py ownership + testing section).

### 2026-06-17 ~UTC — Add `mcts` baseline agent (Monte Carlo Tree Search)
- New built-in `MctsAgent` (`src/ai_arena/agents/mcts.py`): game-agnostic UCT
  MCTS using ONLY the Game protocol — selection (negamax UCT), expansion, a
  uniform random rollout to terminal / `rollout_depth` horizon, backprop; plays
  the most-visited root child (robust choice). Tracks the mover by alternating
  from the root player (the engine's strict alternation). Defaults: `iterations`
  800, `node_budget` 50k (hard cap on apply_move calls, like search/greedy),
  `rollout_depth` 60, `exploration` 1.4. Seeded (shuffles moves + rollout RNG)
  → fully deterministic. Same robustness contract as search/greedy: always
  returns an element of legal_moves, swallows speculative game-call exceptions,
  scores a no-legal-moves position as a loss for the mover (engine forfeit rule).
- Why it's the next rung above `search`: `search` has no domain heuristic, so
  past its horizon it scores everything 0 and plays shallowly on the big games.
  MCTS substitutes random playouts for that missing heuristic, so it keeps
  improving with simulations on Caldera/Skysummit. KEY CAVEAT (documented):
  MCTS strength tracks how informative random play is — on Photon, where ~35/40
  random games hit the turn-cap DRAW, rollouts carry almost no signal and it
  plays no better than random. Verified random-vs-random terminal mix per game
  before relying on rollouts.
- Wet-tested (full defaults): beats greedy 12-0 on Caldera and 8-4 on Skysummit
  (both seat-swapped), 18/20 vs random on tictactoe (0 losses), draws itself 20/20,
  near-perfect vs `search` (draws ~9/10, the rare loss is random-rollout
  imperfection vs a perfect solver — NOT claimed to "never lose" tictactoe like
  search does). Per-move ~160-680ms on Caldera (budget-bounded; comparable to
  search). CLI/tournament/benchmark all drive it cleanly.
- Wired into all loaders (cli/tournament/gui/benchmark) + `list-agents` +
  `--seed`; help text + `_BUILTIN_AGENTS` + `agents/__init__.__all__` updated
  (cli drift-guard test covers the new name). Docs: README baselines, both
  ARCHITECTURE agent + testing sections, docs/protocol.md.
- Tests: tests/test_mcts.py (17: tactics — take win / block / forced win /
  starve-opponent; tictactoe strength — never-loses-random, beats-random,
  two-MCTS-mostly-draw, beats-greedy; invariants — legal-move, determinism,
  seed-variety, single-move short-circuit, never-raises, inner-speculation
  swallowed, node-budget bound; end-to-end clean play + Caldera generality).
  139 → 156 tests, suite ~5.3s → ~10s.

### 2026-06-17 ~UTC — Add `benchmark` command (head-to-head agent eval)
- New `ai-arena benchmark <game> --p0 <a> --p1 <b> --games N [--seed S]
  [--no-swap] [--out f] [--quiet]` (`src/ai_arena/benchmark.py`): plays N
  independent matches between two agents and reports outcomes **by contestant**,
  not by seat. Formalizes the manual benchmarking the claudepad was full of
  ("search vs random = 0 losses over 100 games", etc.).
- Seats alternate by default (`swap_starts`) so first-mover advantage cancels;
  `--seed` gives every game distinct, reproducible seeds (game i seeds the two
  seats `S+2i` / `S+2i+1`). Fresh agent per game (independent games; stateful
  bots not replayed). Reports W/D counts + %, forfeit tally (timeout/
  agent_error/illegal_move) split by contestant, a terminal-reason histogram
  (shows *how* games end — crown_captured / reach_level3 / draw …), avg turns,
  and per-contestant think-time (avg/max). `--out` writes JSON atomically.
  `human` rejected (blocks on stdin).
- Durability: each game runs under containment — Ctrl-C, a flaky subprocess
  spawn failure, or a game-code crash stops the run but **keeps every completed
  game** and returns `incomplete=True` (exit 130), never discarding an
  expensive run wholesale. (This was the one finding from the high-effort
  multi-agent review; the rest of the module verified clean — seat/forfeit/seed/
  throttle/empty-stats/closure logic all correct.)
- Self-contained spec resolver `_seeded_agent_factory` (reuses tournament's
  `_game_factory`); the human/subprocess/path branches duplicate cli/tournament
  by design — the seed-per-game signature `Callable[[int|None],Any]` differs
  from both, and merging would mean refactoring tested code.
- Wet-tested: search vs random = 0 losses (80W/20D) on tictactoe; search-v-search
  = all draws; greedy beats random on Caldera (crown_captured) and Skysummit
  (reach_level3); determinism reproduces; bad subprocess spec → warning + clean
  partial summary + exit 130 (was an unhandled traceback).
- Tests: tests/test_benchmark.py (15: outcome attribution, seat-swap balancing
  via a FirstMoverWins game, forfeit attribution, seeded reproducibility,
  partial-on-interrupt, partial-on-spawn-failure, summary formatting, CLI).
  124 → 139 tests, suite ~3.8s.

### 2026-06-17 ~UTC — Add `search` baseline agent (depth-limited alpha-beta)
- New built-in `SearchAgent` (`src/ai_arena/agents/search.py`): game-agnostic
  negamax with alpha-beta, using ONLY the Game protocol. Leaves scored by
  terminal outcome only (`+(WIN+depth)` win / `-(WIN+depth)` loss-or-no-moves /
  `0` draw / `0` at the depth horizon). With no domain heuristic it plays
  perfectly on games small enough to search to the end (never loses tic-tac-toe;
  two searchers always draw) and behaves like a deeper-horizon `greedy` on the
  big arena games. Defaults: `max_depth=12`, `node_budget=40_000` (bounds
  worst-case cost like greedy's `safety_budget`; its alpha-beta search of
  tic-tac-toe costs ~21k node expansions from the opening worst case, well under
  budget, so it never truncates there). Seeded
  shuffle for deterministic tie-breaks; swallows speculative game-call
  exceptions and always returns an element of `legal_moves`.
- Wired into all loaders (cli/tournament/gui) + `list-agents` + `--seed`; help
  text and `_BUILTIN_AGENTS` updated (the cli drift-guard test now covers it).
  Also added `GreedyAgent`+`SearchAgent` to `agents/__init__.__all__` (greedy was
  a prior omission).
- Wet-tested: search(p0) vs random = 0 losses over 100 tictactoe games (both
  seats); search vs greedy = 0 losses, wins net; search-vs-search = 30/30 draws;
  finds mate-in-1 and a 3-ply forced win greedy's 1-ply horizon misses; CLI
  matches on Caldera/Skysummit complete cleanly; same-seed runs reproduce; a
  search-vs-random tournament scores correctly (search 5W/0L/1D). Per-move time
  budget-bounded: ~0.15-1.3s on the heavy games.
- Verified there is NO infinite-loop bug in Photon's `fire_lasers` (the laser
  step is injective/reversible on the finite (pos,dir) state space, so a beam
  can only exit or return to its own shooter and break; 200k random configs cap
  at path length 40). Left the laser code as-is.
- Tests: tests/test_search.py (16 tests: tactics, optimal tic-tac-toe vs
  random/greedy, two-searchers-draw, legal-move invariant, determinism,
  node-budget bound, inner-exception robustness, Caldera generality). 100%
  coverage of search.py. 109 → 124 tests, suite ~3.1s.

### 2026-06-17 ~UTC — Add `greedy` baseline agent + reproducible seeding
- New built-in `GreedyAgent` (`src/ai_arena/agents/greedy.py`): game-agnostic,
  uses ONLY the Game protocol. Per turn: (1) grab an immediate win; (2) else
  avoid moves that let the opponent win next ply OR that lose on the spot
  (covers Caldera mutual-eruption / Photon self-laser); (3) else random.
  Shuffles via a seeded RNG for uniform, reproducible tie-breaks. Shallow
  (1 ply each way) by design — a real skill floor above `random`, not a solver.
  `safety_budget` (default 12k) bounds the O(branching^2) defence scan on
  high-branching external games; unverified candidates are "unknown", never
  silently "safe". Never preferred-forfeits: swallows speculative game-call
  exceptions, always returns an element of legal_moves.
- `RandomAgent` is now seedable (`seed=None` keeps old nondeterministic
  behaviour via `random.Random(None)`); both agents take `seed`.
- CLI: new `list-agents`; `play --seed N` threads a seed to built-in
  random/greedy agents (p0=seed, p1=seed+1) for a reproducible match. `greedy`
  wired into all three loaders (cli/tournament/gui).
- Wet-tested: greedy(p0) vs random = 181W/1L/18D over 200 tictactoe games;
  full greedy-vs-random matches complete cleanly on Caldera (capture win),
  Skysummit (reach-3 win), Photon (survives to max-turn draw); per-turn cost
  1–74 ms; tournament with greedy integrates and scores correctly; same-seed
  runs produce identical move sequences.
- Tests: tests/test_greedy.py (win-grab, block, self-loss avoidance, legal-move
  invariant over 200 positions, seed determinism, never-raises-on-bad-game,
  cross-game generality on Caldera) + a cli drift guard. 97 → 109 tests.
- Multi-agent review (line-by-line + caller-tracer + pitfalls/reuse): no
  correctness bugs; fixed a docstring overclaim ("never raises" → scoped to the
  engine's non-empty-legal contract) and added the list-agents drift test.

### 2026-06-11 ~00:30 UTC — Durable run artifacts (logs survive crashes)
- Match logs are now incremental + atomic: `play_match` snapshots the log as
  moves apply (`reason: "in_progress"`, throttled ~1/s via
  `log_snapshot_interval_s`), so a game crash / Ctrl-C / SIGKILL keeps the
  history; previously a mid-match crash lost everything (match_error voided
  with no log). Final write replaces the stub; partial logs replay fine.
- `atomic_write_json` (engine): unique mkstemp temp + fsync + rename, umask
  perms restored (mkstemp's 0600 broke other readers), temp cleaned on
  failure; `default=repr` is opt-in (match logs only). Shared by engine,
  tournament, and GUI save-log.
- Tournament `--out` results now written before match 1 (fences stale data
  from a prior run; only after fail-fast spec validation so a config typo
  can't clobber a previous run's results) and after every match
  (`complete: false` until done). Snapshot/results writes are BEST-EFFORT
  (warn to stderr, play on) — durability must never abort the run it
  protects.
- New tests: tests/test_match_log.py (partial-log survival, throttle,
  best-effort snapshot, repr fallback), tests/test_cli.py (first CLI
  coverage), tournament fence/incremental/write-failure tests; shared
  FirstLegalAgent moved to tests/helpers.py. 84 → 97 tests.
- Wet-tested: SIGKILL mid-tournament leaves parseable results.json
  (complete:false, finished matches scored) + in_progress log for the
  in-flight match; replayed a partial log through GUI inference.
- Multi-agent review (9 finders + sweep) drove the hardening: unguarded
  snapshot writes, results-write containment, fsync, tmp-name race, O(n²)
  rewrite, double-asdict, perms regression, fence-before-validation were all
  found and fixed this session.

### 2026-06-10 ~08:40 UTC — Harness robustness pass
- Fixed GUI log replay: `_infer_game_spec_from_log` used stale names
  (`opus_game`, `gemini_game`); real names are `caldera` and
  `photon_laser_tactics`, so those logs couldn't be replayed without `--game`.
- Fixed SubprocessAgent stderr deadlock: stderr was piped but never read, so a
  bot writing >64 KiB to stderr blocked forever and timed out. Now drained on
  a background thread; the tail is attached to errors when a bot dies
  (previously a startup crash surfaced only as "bot stdout closed").
- Tournament crash containment: specs still resolve eagerly (typos fail
  fast), but per-match crashes no longer kill the run — agent spawn failure
  forfeits to the opponent (`agent_spawn_failed:...`), game-code crashes void
  the match (`match_error:...`, no points).
- Unified engine forfeit semantics: `MatchResult.turns` now counts applied
  moves on every path (illegal_move used to count the failed attempt).
- All verified: 84 tests pass; wet-tested CLI play on all four games, a full
  3-competitor random-agent tournament (18 matches), and log replay through
  the GUI inference path.
- Multi-agent code review (9 finder angles + verify + sweep) on the diff
  found and fixed: match_error outcomes now record the constructed game's
  real name (spec label only when construction fails, reason text collapsed
  to one line); GUI `_write_log` turns now counts applied moves like the
  engine; `SubprocessAgent.close()` getattr-guards half-built instances;
  deduped test agents and the third-scenario tuple.

## Key Findings

- Game names are load-bearing: engine logs store `game.name`
  (`skysummit`, `caldera`, `photon_laser_tactics`, `tictactoe`), and GUI
  replay maps those names back to specs in `_infer_game_spec_from_log`.
  `tests/test_gui_infer.py` loads each inferred spec and checks `.name` so a
  rename breaks the test, not replay.
- Tournament matches are expensive (every move can be an LLM API call, up to
  an hour per turn) — that's why per-match containment and the per-match logs
  in `log_dir` matter: results survive a mid-run crash.
- `gemini/game/game.py` does `sys.path.insert(0, <its dir>)` at import to
  reach its sibling `board.py`, registering generic top-level module names
  (`board`, `agent`, `gui`). Latent collision risk if another model folder
  ever does the same; harmless today. Left as-is (competitor code).
- macOS pipe buffer is 64 KiB — the SubprocessAgent stderr deadlock was
  reproduced empirically before fixing (bot blocked on stderr write, no
  stdout within 3s).
