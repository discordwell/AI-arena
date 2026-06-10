# Claudepad

## Session Summaries

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
