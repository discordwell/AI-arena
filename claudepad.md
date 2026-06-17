# Claudepad

## Session Summaries

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
