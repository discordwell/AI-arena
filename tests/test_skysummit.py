from __future__ import annotations

from ai_arena.loading import load_symbol


def _game():
    return load_symbol("codex/game/game.py:CodexGame")()


def test_skysummit_initial_state() -> None:
    g = _game()
    s = g.initial_state()
    assert s["phase"] == "place"
    assert s["ply"] == 0
    assert len(s["board"]) == 25
    assert s["workers"] == [[None, None], [None, None]]
    assert s["winner"] is None


def test_skysummit_placement_legal_moves_count() -> None:
    g = _game()
    s = g.initial_state()
    moves = g.legal_moves(s, 0)
    # We allow both orderings of the placement pair: 2 * C(25, 2) = 600.
    assert len(moves) == 600


def test_skysummit_place_then_play_transition() -> None:
    g = _game()
    s = g.initial_state()

    s = g.apply_move(s, 0, {"t": "place", "to": [1, 0]})
    assert s["phase"] == "place"
    assert s["workers"][0] == [0, 1]

    s = g.apply_move(s, 1, {"t": "place", "to": [24, 23]})
    assert s["phase"] == "play"
    assert s["workers"][1] == [23, 24]


def test_skysummit_winning_move_build_is_none() -> None:
    g = _game()
    board = [0] * 25
    board[0] = 2
    board[6] = 3  # adjacent to 0; climb +1 => legal winning move
    s = {
        "phase": "play",
        "ply": 0,
        "board": board,
        "workers": [[0, 1], [23, 24]],
        "winner": None,
        "reason": "",
    }

    legal = g.legal_moves(s, 0)
    win_move = {"t": "move", "w": 0, "to": 6, "build": None}
    assert win_move in legal

    s2 = g.apply_move(s, 0, win_move)
    t = g.terminal(s2)
    assert t.is_terminal
    assert t.winner == 0
    assert t.reason == "reach_level3"


def test_skysummit_turn_limit_tiebreak() -> None:
    g = _game()
    board = [0] * 25
    board[0] = 2
    board[1] = 1
    s = {
        "phase": "play",
        "ply": g.max_ply,
        "board": board,
        "workers": [[0, 1], [23, 24]],
        "winner": None,
        "reason": "",
    }

    t = g.terminal(s)
    assert t.is_terminal
    assert t.winner == 0
    assert t.reason == "turn_limit"


def test_skysummit_all_reported_legal_moves_apply_cleanly() -> None:
    g = _game()
    s = g.initial_state()
    s = g.apply_move(s, 0, {"t": "place", "to": [0, 6]})
    s = g.apply_move(s, 1, {"t": "place", "to": [18, 24]})
    assert s["phase"] == "play"

    legal = g.legal_moves(s, 0)
    assert legal
    for m in legal:
        s2 = g.apply_move(s, 0, m)
        # apply_move should never crash for any move it claims is legal.
        assert isinstance(s2, dict)

