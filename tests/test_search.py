"""Tests for the built-in SearchAgent (game-agnostic depth-limited negamax)."""
from __future__ import annotations

import random

from ai_arena.agents.greedy import GreedyAgent
from ai_arena.agents.random_agent import RandomAgent
from ai_arena.agents.search import SearchAgent
from ai_arena.engine import play_match
from ai_arena.game import Terminal
from ai_arena.games.tictactoe import TicTacToe


# ---------------------------------------------------------------------------
# Tactics: immediate wins and multi-ply forced wins
# ---------------------------------------------------------------------------


def test_takes_immediate_win() -> None:
    game = TicTacToe()
    # Player 0 (X = 1) has two in the top row; cell 2 completes it.
    state = {"board": [1, 1, 0, 2, 2, 0, 0, 0, 0]}
    legal = game.legal_moves(state, 0)
    for seed in range(20):
        assert SearchAgent(seed=seed).select_move(game, state, 0, legal) == 2


class _MateInTwoGame:
    """
    The mover wins only by looking three plies ahead:

      step 0 (mover):    "setup" -> step 1 (live) ; "draw" -> terminal draw
      step 1 (opponent): "forced" -> step 2 (live)        [only legal move]
      step 2 (mover):    "finish" -> terminal, mover wins  [only legal move]

    A 1-ply (greedy) agent sees both step-0 moves as merely "safe"; only a
    >=3-ply search prefers "setup".
    """

    name = "mate2"

    def initial_state(self) -> dict:
        return {"step": 0, "winner": None, "hero": None}

    def legal_moves(self, state, player):
        if self.terminal(state).is_terminal:
            return []
        return {0: ["setup", "draw"], 1: ["forced"], 2: ["finish"]}.get(state["step"], [])

    def apply_move(self, state, player, move):
        if move == "setup":
            return {"step": 1, "winner": None, "hero": player}
        if move == "draw":
            return {"step": 9, "winner": None, "hero": None}
        if move == "forced":
            return {"step": 2, "winner": None, "hero": state["hero"]}
        if move == "finish":
            return {"step": 9, "winner": state["hero"], "hero": state["hero"]}
        raise ValueError(f"illegal move: {move!r}")

    def terminal(self, state):
        if state["winner"] is not None:
            return Terminal(True, state["winner"], "win")
        if state["step"] >= 9:
            return Terminal(True, None, "draw")
        return Terminal(False, None, "")

    def render(self, state):
        return str(state)


def test_finds_forced_mate_in_two() -> None:
    game = _MateInTwoGame()
    state = game.initial_state()
    legal = ["setup", "draw"]
    # Default depth searches deep enough to see the forced win regardless of seed.
    for seed in range(16):
        assert SearchAgent(seed=seed).select_move(game, state, 0, legal) == "setup"


def test_shallow_search_cannot_see_the_mate() -> None:
    game = _MateInTwoGame()
    state = game.initial_state()
    legal = ["setup", "draw"]
    # The win is 3 plies away, so a depth-2 search scores both moves 0 and can
    # only fall back to the (seeded) tie-break -- it has no real preference for
    # the win. Both moves appearing across seeds proves the choice is pure
    # tie-break (a depth>=3 search would always pick "setup"); see
    # test_finds_forced_mate_in_two for the deep-search contrast.
    picks = {SearchAgent(seed=s, max_depth=2).select_move(game, state, 0, legal) for s in range(24)}
    assert picks == {"setup", "draw"}


def test_no_legal_moves_for_opponent_is_a_win() -> None:
    class _StarveGame:
        """Mover can leave the opponent with no legal move (an arena loss for them)."""

        name = "starve"

        def initial_state(self):
            return {"s": 0}

        def legal_moves(self, state, player):
            return {0: ["starve", "pass"], 1: [], 2: ["end"]}.get(state["s"], [])

        def apply_move(self, state, player, move):
            return {"starve": {"s": 1}, "pass": {"s": 2}, "end": {"s": 3}}[move]

        def terminal(self, state):
            return Terminal(True, None, "draw") if state["s"] >= 3 else Terminal(False, None, "")

        def render(self, state):
            return str(state)

    game = _StarveGame()
    state = game.initial_state()
    for seed in range(10):
        # "starve" leaves the opponent with no reply (they forfeit) -> a win,
        # which must beat the drawing "pass".
        assert SearchAgent(seed=seed).select_move(game, state, 0, ["starve", "pass"]) == "starve"


# ---------------------------------------------------------------------------
# Optimality on tic-tac-toe (small enough to search to the end)
# ---------------------------------------------------------------------------


def _play_many(make0, make1, n: int) -> list[int]:
    """Return [p0_wins, p1_wins, draws] over n tic-tac-toe matches."""
    tally = [0, 0, 0]
    for i in range(n):
        res = play_match(TicTacToe(), make0(i), make1(i))
        tally[2 if res.winner is None else res.winner] += 1
    return tally


def test_never_loses_tictactoe_vs_random() -> None:
    # A perfect player never loses tic-tac-toe; verify from both seats.
    as_p0 = _play_many(lambda i: SearchAgent(seed=i), lambda i: RandomAgent(seed=1000 + i), 20)
    assert as_p0[1] == 0, f"search lost as p0: {as_p0}"
    as_p1 = _play_many(lambda i: RandomAgent(seed=1000 + i), lambda i: SearchAgent(seed=i), 20)
    assert as_p1[0] == 0, f"search lost as p1: {as_p1}"


def test_never_loses_and_beats_greedy_on_tictactoe() -> None:
    as_p0 = _play_many(lambda i: SearchAgent(seed=i), lambda i: GreedyAgent(seed=1000 + i), 15)
    as_p1 = _play_many(lambda i: GreedyAgent(seed=1000 + i), lambda i: SearchAgent(seed=i), 15)
    search_losses = as_p0[1] + as_p1[0]
    search_wins = as_p0[0] + as_p1[1]
    assert search_losses == 0, f"search lost to greedy: p0={as_p0} p1={as_p1}"
    # Deeper lookahead must convert at least some of greedy's blunders into wins.
    assert search_wins >= 1, f"search never beat greedy: p0={as_p0} p1={as_p1}"


def test_two_searchers_always_draw_tictactoe() -> None:
    res = _play_many(lambda i: SearchAgent(seed=i), lambda i: SearchAgent(seed=500 + i), 10)
    assert res[0] == 0 and res[1] == 0, f"optimal play should draw, got {res}"


# ---------------------------------------------------------------------------
# Invariants: always legal, deterministic under a seed, never raises, bounded
# ---------------------------------------------------------------------------


def test_always_returns_a_legal_move() -> None:
    game = TicTacToe()
    rng = random.Random(123)
    # The legal-move invariant is independent of search depth/budget, so use a
    # small budget here to keep the position walk fast.
    agent = SearchAgent(seed=7, node_budget=1500)
    for _ in range(40):
        state = game.initial_state()
        player = 0
        while not game.terminal(state).is_terminal:
            legal = game.legal_moves(state, player)
            if not legal:
                break
            assert agent.select_move(game, state, player, legal) in legal
            state = game.apply_move(state, player, rng.choice(legal))
            player = 1 - player


def test_same_seed_is_deterministic() -> None:
    game = TicTacToe()
    state = game.initial_state()
    legal = game.legal_moves(state, 0)
    a = SearchAgent(seed=42).select_move(game, state, 0, legal)
    b = SearchAgent(seed=42).select_move(game, state, 0, legal)
    assert a == b


def test_different_seeds_can_differ() -> None:
    # On the open board every move draws under perfect play, so all moves are
    # equal-valued and the seeded tie-break decides -- which must vary by seed.
    game = TicTacToe()
    state = game.initial_state()
    legal = game.legal_moves(state, 0)
    picks = {SearchAgent(seed=s).select_move(game, state, 0, legal) for s in range(16)}
    assert len(picks) > 1


def test_select_move_never_raises_on_misbehaving_game() -> None:
    class _ExplodingGame:
        name = "boom"

        def initial_state(self):
            return {}

        def legal_moves(self, state, player):
            raise RuntimeError("speculation should be swallowed")

        def apply_move(self, state, player, move):
            raise RuntimeError("speculation should be swallowed")

        def terminal(self, state):
            raise RuntimeError("speculation should be swallowed")

        def render(self, state):
            return ""

    move = SearchAgent(seed=1).select_move(_ExplodingGame(), {}, 0, ["a", "b", "c"])
    assert move in {"a", "b", "c"}  # falls back to a legal move, no exception


def test_inner_speculation_errors_are_swallowed() -> None:
    class _FlakyGame:
        """The root move applies cleanly; deeper speculative calls then raise,
        exercising the guards inside the recursion (terminal/legal/apply)."""

        name = "flaky"

        def __init__(self, fail: str) -> None:
            self.fail = fail  # "terminal" | "legal" | "apply"

        def initial_state(self):
            return {"d": 0}

        def legal_moves(self, state, player):
            if state["d"] == 0:
                return ["go"]
            if self.fail == "legal":
                raise RuntimeError("legal boom")
            return ["a", "b"]

        def apply_move(self, state, player, move):
            if state["d"] == 0:
                return {"d": 1}  # root move: always clean
            if self.fail == "apply":
                raise RuntimeError("apply boom")
            return {"d": 2}

        def terminal(self, state):
            if state["d"] >= 1 and self.fail == "terminal":
                raise RuntimeError("terminal boom")
            if state["d"] >= 2:
                return Terminal(True, None, "draw")
            return Terminal(False, None, "")

        def render(self, state):
            return str(state)

    for fail in ("terminal", "legal", "apply"):
        game = _FlakyGame(fail)
        move = SearchAgent(seed=0).select_move(game, {"d": 0}, 0, ["go"])
        assert move == "go", f"mode {fail!r} should still return the only legal move"


def test_respects_node_budget() -> None:
    # A tiny budget must still yield a legal move and stop expanding promptly.
    from ai_arena.loading import load_symbol

    game = load_symbol("opus/game/game.py:OpusGame")()
    state = game.initial_state()
    legal = game.legal_moves(state, 0)
    agent = SearchAgent(seed=0, node_budget=200, max_depth=12)
    move = agent.select_move(game, state, 0, legal)
    assert move in legal
    assert agent._nodes <= agent.node_budget


# ---------------------------------------------------------------------------
# End-to-end: clean play, and generality across a competitor game
# ---------------------------------------------------------------------------


def test_search_vs_random_completes_cleanly() -> None:
    res = play_match(TicTacToe(), SearchAgent(seed=1), RandomAgent(seed=2))
    assert res.reason in {"win", "draw"}
    assert all(r.note is None for r in res.move_history)  # search never forfeits


def test_search_is_game_agnostic_on_caldera() -> None:
    # No Caldera-specific code in SearchAgent: it drives a full match purely
    # through the Game protocol and never forfeits on an illegal move.
    from opus.game.game import OpusGame

    # A modest budget keeps the full match fast; generality, not depth, is the
    # point -- the agent must drive Caldera through the protocol without forfeit.
    res = play_match(OpusGame(), SearchAgent(seed=3, node_budget=4000), RandomAgent(seed=4))
    assert res.turns >= 1
    assert res.reason
    assert all(r.note is None for r in res.move_history)
