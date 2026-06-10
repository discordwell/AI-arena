from __future__ import annotations

import json

from ai_arena.loading import load_symbol


def _game():
    return load_symbol("gemini/game/game.py:GeminiGame")()


def test_photon_initial_legal_moves_exact_count() -> None:
    g = _game()
    s = g.initial_state()

    # Hand-counted from the fixed initial setup, per player:
    # moves: King 2 + Shooters 4+4 + Mirrors 4+4 + Block 3 = 21
    # rotates: (2 Shooters + 2 Mirrors) * 2 directions = 8
    for player in (0, 1):
        moves = g.legal_moves(s, player)
        move_actions = [m for m in moves if m["type"] == "move"]
        rotate_actions = [m for m in moves if m["type"] == "rotate"]
        assert len(move_actions) == 21
        assert len(rotate_actions) == 8


def test_photon_legal_moves_src_always_holds_own_piece() -> None:
    # Regression: legal_moves used to mutate its scratch board via move_piece(),
    # letting pieces "walk" during generation. That dropped real moves and
    # emitted phantom moves whose src square is empty in the actual state.
    g = _game()
    s = g.initial_state()

    for player in (0, 1):
        for m in g.legal_moves(s, player):
            r, c = m["src"]
            piece = s["board"][r][c]
            assert piece is not None, f"phantom move from empty square: {m}"
            assert piece["player"] == player, f"move uses opponent piece: {m}"


def test_photon_legal_moves_does_not_mutate_state() -> None:
    g = _game()
    s = g.initial_state()
    before = json.dumps(s, sort_keys=True)
    g.legal_moves(s, 0)
    g.legal_moves(s, 1)
    assert json.dumps(s, sort_keys=True) == before


def test_photon_legal_moves_unique() -> None:
    g = _game()
    s = g.initial_state()
    for player in (0, 1):
        moves = g.legal_moves(s, player)
        keys = {json.dumps(m, sort_keys=True) for m in moves}
        assert len(keys) == len(moves)


def test_photon_king_has_both_lateral_moves_at_start() -> None:
    # Regression: the scratch-board bug made the king "walk" east during
    # generation, so its real eastward move was missing from the list.
    g = _game()
    s = g.initial_state()
    king_dsts = sorted(m["dst"] for m in g.legal_moves(s, 0) if m["src"] == [0, 4])
    assert king_dsts == [[0, 3], [0, 5]]


def test_photon_all_reported_legal_moves_apply_cleanly() -> None:
    g = _game()
    s = g.initial_state()

    legal = g.legal_moves(s, 0)
    assert legal
    for m in legal:
        s2 = g.apply_move(s, 0, m)
        # apply_move should never crash for any move it claims is legal.
        assert isinstance(s2, dict)
        if m["type"] == "move":
            r, c = m["src"]
            # The piece always leaves its source square (it may then be
            # destroyed at the destination by the same-turn laser phase).
            assert s2["board"][r][c] is None, f"move did not vacate src: {m}"


def test_photon_turn_limit_is_just_over_30_moves() -> None:
    # Pins the tournament-speed cap so rules.md and the code stay in sync:
    # the game is drawn once turn_count exceeds 30.
    g = _game()
    s = g.initial_state()

    s["turn_count"] = 30
    assert not g.terminal(s).is_terminal

    s["turn_count"] = 31
    t = g.terminal(s)
    assert t.is_terminal
    assert t.winner is None
    assert t.reason == "Max turns reached"


def test_photon_terminal_on_king_elimination() -> None:
    g = _game()
    s = g.initial_state()

    # Remove player 1's king directly from the state.
    board = s["board"]
    king_pos = None
    for r, row in enumerate(board):
        for c, cell in enumerate(row):
            if cell is not None and cell["type"] == "K" and cell["player"] == 1:
                king_pos = (r, c)
    assert king_pos is not None
    board[king_pos[0]][king_pos[1]] = None

    t = g.terminal(s)
    assert t.is_terminal
    assert t.winner == 0
