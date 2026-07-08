from __future__ import annotations

import pytest

from ai_arena.game import Terminal
from ai_arena.games.tictactoe import TicTacToe
from ai_arena.loading import load_symbol
from ai_arena.replay import (
    infer_game_spec_from_log,
    replay_from_log_payload,
    replay_from_move_history,
)


class _LenientGame:
    """A game whose apply_move ignores the move instead of raising on an illegal
    one -- the class of game for which replay's legality re-check earns its keep
    (a strict game would raise from apply_move on its own)."""

    name = "lenient"

    def initial_state(self) -> dict:
        return {"n": 0}

    def legal_moves(self, state: dict, player: int) -> list:
        return [1]

    def apply_move(self, state: dict, player: int, move) -> dict:
        return {"n": state["n"] + 1}  # advances regardless of `move`

    def terminal(self, state: dict) -> Terminal:
        return Terminal(state["n"] >= 5, None, "cap" if state["n"] >= 5 else "")

    def render(self, state: dict) -> str:
        return str(state["n"])


def test_infer_game_spec_resolves_known_names_and_ignores_unknown() -> None:
    # Canonical home of the inferrer (gui re-exports it). Each known name must
    # load to a game whose .name matches, so a rename can't silently break it.
    assert infer_game_spec_from_log({"game": "tictactoe"}) == "tictactoe"
    assert infer_game_spec_from_log({"result": {"game": "tictactoe"}}) == "tictactoe"
    for name in ("skysummit", "caldera", "photon_laser_tactics"):
        spec = infer_game_spec_from_log({"game": name})
        assert spec is not None
        assert load_symbol(spec)().name == name

    # Unknown / malformed payloads yield None rather than raising.
    assert infer_game_spec_from_log({"game": "no_such_game"}) is None
    assert infer_game_spec_from_log({}) is None
    assert infer_game_spec_from_log({"result": "not-a-dict"}) is None


def test_replay_reconstructs_states_and_terminal() -> None:
    game = TicTacToe()
    # X (p0) wins on the top row: 0,1,2
    move_history = [
        {"turn": 1, "player": 0, "move": 0, "ms": 0.0, "note": None},
        {"turn": 2, "player": 1, "move": 3, "ms": 0.0, "note": None},
        {"turn": 3, "player": 0, "move": 1, "ms": 0.0, "note": None},
        {"turn": 4, "player": 1, "move": 4, "ms": 0.0, "note": None},
        {"turn": 5, "player": 0, "move": 2, "ms": 0.0, "note": None},
    ]

    rep = replay_from_move_history(game, move_history)
    assert len(rep.states) == len(move_history) + 1
    assert rep.terminal.is_terminal
    assert rep.terminal.winner == 0
    assert rep.terminal.reason == "win"


def test_replay_rejects_illegal_logged_move_on_a_lenient_game() -> None:
    # An engine log only ever contains legal applied moves, so a genuine log
    # replays fine; a corrupt/tampered one whose applied move is not legal in the
    # reconstructed state must be surfaced. A strict game raises from apply_move,
    # but a lenient game would silently apply the bad move and reconstruct a
    # *wrong* state -- so replay checks legality explicitly.
    game = _LenientGame()

    ok = [{"turn": 1, "player": 0, "move": 1, "ms": 0.0, "note": None}]
    assert replay_from_move_history(game, ok).states[-1] == {"n": 1}  # legal: fine

    tampered = ok + [{"turn": 2, "player": 1, "move": 99, "ms": 0.0, "note": None}]  # 99 not in [1]
    with pytest.raises(ValueError, match="not legal in the reconstructed state"):
        replay_from_move_history(game, tampered)


def test_replay_handles_illegal_move_forfeit_from_log_payload() -> None:
    game = TicTacToe()
    payload = {
        "game": "tictactoe",
        "result": {
            "game": "tictactoe",
            "winner": 0,
            "reason": "illegal_move",
            "turns": 2,
            "move_history": [
                {"turn": 1, "player": 0, "move": 0, "ms": 0.0, "note": None},
                {"turn": 2, "player": 1, "move": 999, "ms": 0.0, "note": "illegal_move"},
            ],
        },
    }

    rep = replay_from_log_payload(game, payload)
    assert len(rep.states) == 3  # initial + applied + unchanged-forfeit frame
    assert rep.states[1] == rep.states[2]
    assert rep.terminal.is_terminal
    assert rep.terminal.winner == 0
    assert rep.terminal.reason == "illegal_move"


def test_replay_of_in_progress_snapshot_log() -> None:
    # The engine rewrites the log after every applied move with a stub result
    # (reason "in_progress") so crashed matches stay replayable. The replay
    # falls back to that engine result when game rules say non-terminal.
    game = TicTacToe()
    payload = {
        "game": "tictactoe",
        "result": {
            "game": "tictactoe",
            "winner": None,
            "reason": "in_progress",
            "turns": 2,
            "move_history": [
                {"turn": 1, "player": 0, "move": 0, "ms": 0.0, "note": None},
                {"turn": 2, "player": 1, "move": 3, "ms": 0.0, "note": None},
            ],
        },
    }

    rep = replay_from_log_payload(game, payload)
    assert len(rep.states) == 3
    assert rep.terminal.is_terminal  # labeled via the engine-result fallback
    assert rep.terminal.winner is None
    assert rep.terminal.reason == "in_progress"
