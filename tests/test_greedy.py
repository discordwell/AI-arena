"""Tests for the built-in GreedyAgent (game-agnostic 1-ply baseline)."""
from __future__ import annotations

import random

from ai_arena.agents.greedy import GreedyAgent
from ai_arena.agents.random_agent import RandomAgent
from ai_arena.engine import play_match
from ai_arena.game import Terminal
from ai_arena.games.tictactoe import TicTacToe


# ---------------------------------------------------------------------------
# Offence: take an immediate win
# ---------------------------------------------------------------------------


def test_takes_immediate_win() -> None:
    game = TicTacToe()
    # Player 0 (X = 1) has two in the top row; cell 2 completes it.
    state = {"board": [1, 1, 0, 2, 2, 0, 0, 0, 0]}
    legal = game.legal_moves(state, 0)
    move = GreedyAgent(seed=0).select_move(game, state, 0, legal)
    assert move == 2


def test_takes_win_over_a_merely_safe_move() -> None:
    # A winning move must beat a safe-but-non-winning one regardless of seed.
    game = TicTacToe()
    state = {"board": [1, 1, 0, 2, 2, 0, 0, 0, 0]}
    legal = game.legal_moves(state, 0)
    for seed in range(20):
        assert GreedyAgent(seed=seed).select_move(game, state, 0, legal) == 2


# ---------------------------------------------------------------------------
# Defence: avoid letting the opponent win next turn
# ---------------------------------------------------------------------------


def test_blocks_opponent_immediate_win() -> None:
    game = TicTacToe()
    # Opponent (O = 2) threatens to complete the top row at cell 2. Blocking
    # there is the only move that does not hand O the win next turn.
    state = {"board": [2, 2, 0, 1, 0, 0, 1, 0, 0]}
    legal = game.legal_moves(state, 0)
    for seed in range(20):
        assert GreedyAgent(seed=seed).select_move(game, state, 0, legal) == 2


class _SelfLossGame:
    """Minimal game where one of two moves loses the instant it is applied."""

    name = "selfloss"

    def initial_state(self):
        return {"step": 0, "winner": None}

    def legal_moves(self, state, player):
        if self.terminal(state).is_terminal:
            return []
        return ["safe", "boom"] if state["step"] == 0 else ["end"]

    def apply_move(self, state, player, move):
        if move == "boom":
            return {"step": 1, "winner": 1 - player}  # immediate self-loss
        if move == "safe":
            return {"step": 1, "winner": None}
        return {"step": 2, "winner": None}  # "end" -> harmless draw

    def terminal(self, state):
        if state["winner"] is not None:
            return Terminal(True, state["winner"], "loss")
        if state["step"] >= 2:
            return Terminal(True, None, "draw")
        return Terminal(False, None, "")

    def render(self, state):
        return str(state)


def test_avoids_self_destructive_move() -> None:
    game = _SelfLossGame()
    state = game.initial_state()
    legal = game.legal_moves(state, 0)
    for seed in range(10):
        assert GreedyAgent(seed=seed).select_move(game, state, 0, legal) == "safe"


# ---------------------------------------------------------------------------
# Invariants: always legal, deterministic under a seed, never raises
# ---------------------------------------------------------------------------


def test_always_returns_a_legal_move() -> None:
    game = TicTacToe()
    rng = random.Random(123)
    agent = GreedyAgent(seed=7)
    # Walk many random reachable positions and check every greedy pick is legal.
    for _ in range(200):
        state = game.initial_state()
        player = 0
        while not game.terminal(state).is_terminal:
            legal = game.legal_moves(state, player)
            if not legal:
                break
            chosen = agent.select_move(game, state, player, legal)
            assert chosen in legal
            state = game.apply_move(state, player, rng.choice(legal))
            player = 1 - player


def test_same_seed_is_deterministic() -> None:
    game = TicTacToe()
    state = game.initial_state()  # open board: choice falls to the random pool
    legal = game.legal_moves(state, 0)
    a = GreedyAgent(seed=42).select_move(game, state, 0, legal)
    b = GreedyAgent(seed=42).select_move(game, state, 0, legal)
    assert a == b


def test_different_seeds_can_differ() -> None:
    game = TicTacToe()
    state = game.initial_state()
    legal = game.legal_moves(state, 0)
    picks = {GreedyAgent(seed=s).select_move(game, state, 0, legal) for s in range(30)}
    assert len(picks) > 1  # tie-breaking is seed-sensitive, not constant


def test_select_move_never_raises_on_misbehaving_game() -> None:
    class ExplodingGame:
        name = "boom"

        def initial_state(self):
            return {}

        def legal_moves(self, state, player):
            return ["a", "b", "c"]

        def apply_move(self, state, player, move):
            raise RuntimeError("speculation should be swallowed")

        def terminal(self, state):
            return Terminal(False, None, "")

        def render(self, state):
            return ""

    move = GreedyAgent(seed=1).select_move(ExplodingGame(), {}, 0, ["a", "b", "c"])
    assert move in {"a", "b", "c"}  # falls back to a legal move, no exception


# ---------------------------------------------------------------------------
# End-to-end: clean play, and generality across a competitor game
# ---------------------------------------------------------------------------


def test_greedy_vs_random_completes_cleanly() -> None:
    game = TicTacToe()
    res = play_match(game, GreedyAgent(seed=1), RandomAgent(seed=2))
    assert res.reason in {"win", "draw"}
    # Greedy never produces an illegal move or raises, so nothing is forfeited.
    assert all(r.note is None for r in res.move_history)


def test_greedy_is_game_agnostic_on_caldera() -> None:
    # No Caldera-specific code in GreedyAgent: it drives a full match purely
    # through the Game protocol and never forfeits on an illegal move.
    from opus.game.game import OpusGame

    res = play_match(OpusGame(), GreedyAgent(seed=3), RandomAgent(seed=4))
    assert res.turns >= 1
    assert res.reason  # some terminal reason was recorded
    assert all(r.note is None for r in res.move_history)


# ---------------------------------------------------------------------------
# RandomAgent reproducibility (the seed hook GreedyAgent shares)
# ---------------------------------------------------------------------------


def test_random_agent_seed_is_reproducible() -> None:
    game = TicTacToe()
    state = game.initial_state()
    legal = game.legal_moves(state, 0)
    seq_a = [RandomAgent(seed=99).select_move(game, state, 0, legal) for _ in range(1)]
    seq_b = [RandomAgent(seed=99).select_move(game, state, 0, legal) for _ in range(1)]
    assert seq_a == seq_b
