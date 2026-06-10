from __future__ import annotations

from ai_arena.engine import play_match
from ai_arena.games.tictactoe import TicTacToe


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


class FirstLegalAgent:
    name = "first"

    def select_move(self, game, state, player, legal_moves):
        return legal_moves[0]


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
