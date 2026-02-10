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

    class FirstLegalAgent:
        name = "first"

        def select_move(self, game, state, player, legal_moves):
            return legal_moves[0]

    r = play_match(game, IllegalAgent(), FirstLegalAgent())
    assert r.reason == "illegal_move"
    assert r.winner == 1


def test_agent_error_forfeits() -> None:
    game = TicTacToe()

    class FirstLegalAgent:
        name = "first"

        def select_move(self, game, state, player, legal_moves):
            return legal_moves[0]

    r = play_match(game, ExplodingAgent(), FirstLegalAgent())
    assert r.reason == "agent_error"
    assert r.winner == 1
