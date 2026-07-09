from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .game import Game, PlayerId, Terminal
from .json_types import JSONValue

# On-disk marker for a match log snapshot taken mid-match (the final write
# replaces it with the real result). Load-bearing: replay and external tooling
# see this string in result.reason.
REASON_IN_PROGRESS = "in_progress"

# Progress snapshots are throttled so fast (non-LLM) agents don't turn the
# per-move log rewrite into O(n^2) disk churn; any move slower than this gets
# snapshotted, so LLM matches never lose an applied move.
_SNAPSHOT_INTERVAL_S = 1.0


def atomic_write_json(path: Path, payload: Any, *, default: Callable[[Any], str] | None = None) -> None:
    """
    Write JSON durably: unique same-directory temp file, fsynced, then renamed
    over the target. Readers never see a torn file, concurrent writers to the
    same path cannot clobber each other's temp file, and the data is on disk
    before the rename can land. `default` is passed to json.dumps for callers
    that prefer lossy serialization over an unwritable file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, default=default)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        try:
            f = os.fdopen(fd, "w", encoding="utf-8")
        except BaseException:
            os.close(fd)
            raise
        with f:
            f.write(text + "\n")
            f.flush()
            os.fsync(f.fileno())
        # mkstemp creates the file 0600; restore the umask-honoring mode that
        # a plain write would have, so other readers of run artifacts keep
        # working.
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(tmp_name, 0o666 & ~umask)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _is_prime(n: int) -> bool:
    if n <= 1:
        return False
    if n <= 3:
        return True
    if n % 2 == 0 or n % 3 == 0:
        return False
    i = 5
    while i * i <= n:
        if n % i == 0 or n % (i + 2) == 0:
            return False
        i += 6
    return True


@dataclass(frozen=True, slots=True)
class MoveRecord:
    turn: int
    player: PlayerId
    move: JSONValue
    ms: float
    note: str | None = None


@dataclass(frozen=True, slots=True)
class MatchResult:
    game: str
    winner: PlayerId | None
    reason: str
    turns: int  # successfully applied moves; a forfeited attempt is in move_history but not counted
    move_history: list[MoveRecord]


def play_match(
    game: Game,
    agent0: Any,
    agent1: Any,
    *,
    max_turns: int = 10_000,
    prime_pause: bool = False,
    log_path: Path | None = None,
    log_snapshot_interval_s: float = _SNAPSHOT_INTERVAL_S,
) -> MatchResult:
    """
    Run a 2-player, alternating-turn match.

    Agents must implement:
      - name: str
      - select_move(game, state, player, legal_moves) -> JSONValue
    """
    state: JSONValue = game.initial_state()
    history: list[MoveRecord] = []
    player: PlayerId = 0
    last_snapshot: float | None = None
    snapshot_warned = False

    def finish(winner: PlayerId | None, reason: str, turns: int) -> MatchResult:
        result = MatchResult(game=game.name, winner=winner, reason=reason, turns=turns, move_history=history)
        if log_path:
            _write_log(log_path, game, result, state)
        return result

    def snapshot_log(turn: int) -> None:
        # Best-effort by design: a failed progress snapshot must never abort a
        # live match (the strict final write in finish() still surfaces real
        # I/O problems at match end, as it always did).
        nonlocal last_snapshot, snapshot_warned
        if not log_path:
            return
        now = time.monotonic()
        if last_snapshot is not None and now - last_snapshot < log_snapshot_interval_s:
            return
        stub = MatchResult(
            game=game.name, winner=None, reason=REASON_IN_PROGRESS, turns=turn, move_history=history
        )
        try:
            _write_log(log_path, game, stub, state)
            # Stamp after the write completes: if a write itself takes longer
            # than the interval, measuring from its start would let every
            # subsequent move snapshot, re-creating the O(n^2) rewrite.
            last_snapshot = time.monotonic()
        except Exception as e:
            if not snapshot_warned:
                snapshot_warned = True
                print(f"warning: match log snapshot failed ({type(e).__name__}: {e})", file=sys.stderr)

    for turn in range(1, max_turns + 1):
        terminal: Terminal = game.terminal(state)
        if terminal.is_terminal:
            return finish(terminal.winner, terminal.reason, turn - 1)

        agent = agent0 if player == 0 else agent1
        legal = game.legal_moves(state, player)
        if not legal:
            return finish(1 - player, "no_legal_moves", turn - 1)

        t0 = time.perf_counter()
        try:
            move = agent.select_move(game, state, player, legal)
            ms = (time.perf_counter() - t0) * 1000.0
        except TimeoutError as e:
            ms = (time.perf_counter() - t0) * 1000.0
            history.append(MoveRecord(turn=turn, player=player, move=None, ms=ms, note=f"timeout:{e}"))
            return finish(1 - player, "timeout", turn - 1)
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000.0
            history.append(
                MoveRecord(turn=turn, player=player, move=None, ms=ms, note=f"agent_error:{type(e).__name__}:{e}")
            )
            return finish(1 - player, "agent_error", turn - 1)

        if move not in legal:
            history.append(MoveRecord(turn=turn, player=player, move=move, ms=ms, note="illegal_move"))
            return finish(1 - player, "illegal_move", turn - 1)

        state = game.apply_move(state, player, move)
        history.append(MoveRecord(turn=turn, player=player, move=move, ms=ms))

        # Matches can be hours of LLM calls; snapshot the log as the match
        # progresses so a crash anywhere (game bug, Ctrl-C, power) keeps the
        # history so far. The final write replaces the stub result.
        snapshot_log(turn)

        if prime_pause and _is_prime(turn):
            print(f"[prime turn {turn}] extra analysis/coding cycle pause; press Enter to continue...")
            try:
                input()
            except EOFError:
                pass

        player = 1 - player

    # The loop applied `max_turns` moves without the pre-move terminal check
    # ever firing on the resulting state. Evaluate the state the final move
    # produced before declaring the cap: a decisive last move -- a win, or a
    # rules-draw such as a filled board -- lands exactly here, and must be
    # scored as that real outcome, not silently overwritten by an artificial
    # "max_turns" cutoff. (The live GUI already checks terminal before its own
    # max_turns override; this makes the headless engine agree.) Only reached
    # when the cap actually binds, so it costs one extra terminal() call per
    # truncated match, never per move.
    final = game.terminal(state)
    if final.is_terminal:
        return finish(final.winner, final.reason, max_turns)
    return finish(None, "max_turns", max_turns)


def _write_log(path: Path, game: Game, result: MatchResult, final_state: JSONValue) -> None:
    payload = {
        "game": game.name,
        "result": asdict(result),  # recurses into move_history
        "final_state": final_state,
        "final_render": game.render(final_state),
    }
    # default=repr: a buggy in-process agent can put a non-JSON object in the
    # history (recorded on the illegal_move path); the match is forfeit either
    # way, and a lossy log beats an unwritable one.
    atomic_write_json(path, payload, default=repr)
