from __future__ import annotations

from helpers import FirstLegalAgent

from ai_arena.engine import play_match
from ai_arena.game import Terminal
from ai_arena.games.tictactoe import TicTacToe


class ScriptedAgent:
    """Plays a fixed move sequence, falling back to the first legal move."""

    name = "scripted"

    def __init__(self, moves):
        self._moves = list(moves)
        self._i = 0

    def select_move(self, game, state, player, legal_moves):
        move = self._moves[self._i] if self._i < len(self._moves) else legal_moves[0]
        self._i += 1
        return move if move in legal_moves else legal_moves[0]


class _NoMovesForP1Game:
    """P0 always has a move, P1 never does, and terminal() never fires on its
    own -- so only the engine's 'no legal moves' forfeit rule can end the game."""

    name = "nomoves"

    def initial_state(self):
        return {"n": 0}

    def legal_moves(self, state, player):
        return [0] if player == 0 else []

    def apply_move(self, state, player, move):
        return {"n": state["n"] + 1}

    def terminal(self, state):
        return Terminal(is_terminal=False, winner=None, reason="")

    def render(self, state):
        return str(state["n"])


class IllegalAgent:
    name = "illegal"

    def select_move(self, game, state, player, legal_moves):
        return 999


class ExplodingAgent:
    name = "boom"

    def select_move(self, game, state, player, legal_moves):
        raise RuntimeError("kaboom")


class TimeoutAgent:
    name = "slow"

    def select_move(self, game, state, player, legal_moves):
        raise TimeoutError("too slow")


def test_random_vs_random_finishes() -> None:
    game = TicTacToe()

    class RandomAgent:
        name = "random"

        def select_move(self, game, state, player, legal_moves):
            return legal_moves[0]

    r = play_match(game, RandomAgent(), RandomAgent())
    assert r.reason in {"win", "draw"}
    assert r.turns > 0


def test_illegal_move_forfeits() -> None:
    game = TicTacToe()
    r = play_match(game, IllegalAgent(), FirstLegalAgent())
    assert r.reason == "illegal_move"
    assert r.winner == 1


def test_agent_error_forfeits() -> None:
    game = TicTacToe()
    r = play_match(game, ExplodingAgent(), FirstLegalAgent())
    assert r.reason == "agent_error"
    assert r.winner == 1


def test_timeout_forfeits() -> None:
    game = TicTacToe()
    r = play_match(game, FirstLegalAgent(), TimeoutAgent())
    assert r.reason == "timeout"
    assert r.winner == 0


def test_forfeit_turns_count_applied_moves() -> None:
    # `turns` counts successfully applied moves on every forfeit path; the
    # failed attempt itself is recorded in move_history but not counted.
    game = TicTacToe()

    r = play_match(game, FirstLegalAgent(), IllegalAgent())
    assert r.reason == "illegal_move"
    assert r.turns == 1  # p0's applied move; p1's illegal attempt not counted
    assert len(r.move_history) == 2
    assert r.move_history[-1].note == "illegal_move"

    r = play_match(game, FirstLegalAgent(), TimeoutAgent())
    assert r.reason == "timeout"
    assert r.turns == 1
    assert len(r.move_history) == 2

    r = play_match(game, FirstLegalAgent(), ExplodingAgent())
    assert r.reason == "agent_error"
    assert r.turns == 1
    assert len(r.move_history) == 2


def test_no_legal_moves_forfeits() -> None:
    # A player with an empty legal-move list (while the game rules still say the
    # position is non-terminal) forfeits; the opponent wins with reason
    # "no_legal_moves", and `turns` counts the moves that were applied.
    r = play_match(_NoMovesForP1Game(), FirstLegalAgent(), FirstLegalAgent())
    assert r.reason == "no_legal_moves"
    assert r.winner == 0  # P1 (to move) has no moves, so P0 wins
    assert r.turns == 1  # P0's single move was applied before P1 got stuck


def test_max_turns_cutoff_on_a_live_game() -> None:
    # A genuinely non-terminal position at the cap is reported as a max_turns
    # cutoff (winner None), with `turns` == max_turns since every permitted move
    # was applied.
    game = TicTacToe()
    r = play_match(game, ScriptedAgent([0, 1, 2]), ScriptedAgent([3, 4]), max_turns=3)
    assert r.winner is None
    assert r.reason == "max_turns"
    assert r.turns == 3
    assert len(r.move_history) == 3


def test_decisive_win_on_the_final_permitted_turn_is_scored() -> None:
    # Regression: the engine checked `terminal` only *before* each move, so the
    # state produced by the move applied on the max_turns-th ply went
    # unevaluated -- a win landing exactly on the cap was mis-scored as a
    # "max_turns" draw. It must be scored as the real win.
    game = TicTacToe()
    # P0 completes the top row (0,1,2) on its third move -- the 5th applied ply.
    r = play_match(game, ScriptedAgent([0, 1, 2]), ScriptedAgent([3, 4]), max_turns=5)
    assert r.winner == 0
    assert r.reason == "win"
    assert r.turns == 5


def test_rules_draw_on_the_final_permitted_turn_is_scored() -> None:
    # A filled-board draw that lands exactly on the cap is a real "draw", not an
    # artificial "max_turns" cutoff (same boundary as the win case).
    game = TicTacToe()
    # Fills the board with no three-in-a-row; the 9th ply plays the last cell.
    #   X X O
    #   O O X
    #   X O X
    p0 = ScriptedAgent([0, 1, 5, 6, 8])
    p1 = ScriptedAgent([2, 3, 4, 7])
    r = play_match(game, p0, p1, max_turns=9)
    assert r.winner is None
    assert r.reason == "draw"
    assert r.turns == 9
