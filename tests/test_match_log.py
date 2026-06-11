from __future__ import annotations

from pathlib import Path

import pytest
from helpers import FirstLegalAgent

import ai_arena.engine as engine
from ai_arena.engine import play_match
from ai_arena.game import Terminal
from ai_arena.games.tictactoe import TicTacToe
from ai_arena.replay import load_match_log


class NonJsonMoveAgent:
    name = "weird"

    def select_move(self, game, state, player, legal_moves):
        return object()  # not JSON-serializable, and never legal


class CrashOnThirdApplyGame:
    """Counts applied moves in state; apply_move raises on the third one."""

    name = "crash3"

    def initial_state(self):
        return {"n": 0}

    def legal_moves(self, state, player):
        return [0, 1]

    def apply_move(self, state, player, move):
        if state["n"] + 1 >= 3:
            raise RuntimeError("boom on third apply")
        return {"n": state["n"] + 1}

    def terminal(self, state):
        return Terminal(is_terminal=False, winner=None, reason="")

    def render(self, state):
        return f"n={state['n']}"


def test_final_log_written_and_parseable(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "match.json"  # parent dir must be created
    result = play_match(TicTacToe(), FirstLegalAgent(), FirstLegalAgent(), log_path=log)

    payload = load_match_log(log)
    assert payload["game"] == "tictactoe"
    assert payload["result"]["reason"] == result.reason
    assert payload["result"]["reason"] in {"win", "draw"}
    assert payload["result"]["turns"] == result.turns
    assert len(payload["result"]["move_history"]) == len(result.move_history)
    assert "final_state" in payload and "final_render" in payload
    # No atomic-write temp files may linger.
    assert list(log.parent.glob("*.tmp")) == []


def test_partial_log_survives_game_crash(tmp_path: Path) -> None:
    # Matches can be hours of LLM calls. If game code crashes mid-match, the
    # already-played history must survive on disk as an in-progress snapshot.
    # interval=0 snapshots every move (instant moves would otherwise throttle).
    log = tmp_path / "match.json"
    with pytest.raises(RuntimeError, match="boom on third apply"):
        play_match(
            CrashOnThirdApplyGame(),
            FirstLegalAgent(),
            FirstLegalAgent(),
            log_path=log,
            log_snapshot_interval_s=0.0,
        )

    payload = load_match_log(log)
    assert payload["result"]["reason"] == "in_progress"
    assert payload["result"]["winner"] is None
    assert payload["result"]["turns"] == 2
    assert len(payload["result"]["move_history"]) == 2
    assert payload["final_state"] == {"n": 2}
    assert payload["final_render"] == "n=2"


def test_snapshots_throttled_for_fast_moves(tmp_path: Path, monkeypatch) -> None:
    # Only the first applied move snapshots; the rest fall inside the (here:
    # effectively infinite) throttle window, and the final write still lands.
    log = tmp_path / "match.json"
    writes: list[str] = []
    real_write_log = engine._write_log

    def counting_write_log(path, game, result, final_state):
        writes.append(result.reason)
        real_write_log(path, game, result, final_state)

    monkeypatch.setattr(engine, "_write_log", counting_write_log)
    result = play_match(
        TicTacToe(),
        FirstLegalAgent(),
        FirstLegalAgent(),
        log_path=log,
        log_snapshot_interval_s=3600.0,
    )

    assert writes.count("in_progress") == 1
    assert writes[-1] == result.reason
    assert load_match_log(log)["result"]["reason"] == result.reason


def test_snapshot_write_failure_does_not_abort_match(tmp_path: Path, monkeypatch, capsys) -> None:
    # The durability snapshots are best-effort: transient log I/O failures
    # must not void a live match. Only the final write is strict.
    log = tmp_path / "match.json"
    real_write_log = engine._write_log

    def flaky_write_log(path, game, result, final_state):
        if result.reason == "in_progress":
            raise OSError("disk full")
        real_write_log(path, game, result, final_state)

    monkeypatch.setattr(engine, "_write_log", flaky_write_log)
    result = play_match(
        TicTacToe(),
        FirstLegalAgent(),
        FirstLegalAgent(),
        log_path=log,
        log_snapshot_interval_s=0.0,
    )

    assert result.reason in {"win", "draw"}  # match completed normally
    assert load_match_log(log)["result"]["reason"] == result.reason
    assert "match log snapshot failed" in capsys.readouterr().err


def test_log_survives_non_json_move_in_history(tmp_path: Path) -> None:
    # A buggy in-process agent can return any object; it forfeits as an
    # illegal move, and the log must still be writable (repr fallback).
    log = tmp_path / "match.json"
    result = play_match(TicTacToe(), NonJsonMoveAgent(), FirstLegalAgent(), log_path=log)
    assert result.reason == "illegal_move"
    assert result.winner == 1

    payload = load_match_log(log)
    last = payload["result"]["move_history"][-1]
    assert last["note"] == "illegal_move"
    assert isinstance(last["move"], str)  # repr of the non-JSON object
