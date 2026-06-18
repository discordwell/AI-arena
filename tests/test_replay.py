from __future__ import annotations

from ai_arena.games.tictactoe import TicTacToe
from ai_arena.loading import load_symbol
from ai_arena.replay import (
    infer_game_spec_from_log,
    replay_from_log_payload,
    replay_from_move_history,
)


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
