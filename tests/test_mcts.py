"""Tests for the built-in MctsAgent (game-agnostic Monte Carlo Tree Search)."""
from __future__ import annotations

import random

from ai_arena.agents.greedy import GreedyAgent
from ai_arena.agents.mcts import MctsAgent
from ai_arena.agents.random_agent import RandomAgent
from ai_arena.engine import play_match
from ai_arena.game import Terminal
from ai_arena.games.tictactoe import TicTacToe


# ---------------------------------------------------------------------------
# Tactics: immediate wins, blocks, and forced lines
# ---------------------------------------------------------------------------


def test_takes_immediate_win() -> None:
    game = TicTacToe()
    # Player 0 (X = 1) has two in the top row; cell 2 completes it. A winning
    # child is a terminal +1 for the mover in every rollout, so it dominates.
    state = {"board": [1, 1, 0, 2, 2, 0, 0, 0, 0]}
    legal = game.legal_moves(state, 0)
    for seed in range(12):
        assert MctsAgent(seed=seed).select_move(game, state, 0, legal) == 2


def test_blocks_immediate_loss() -> None:
    game = TicTacToe()
    # O (player 1) threatens to complete the top row at cell 2; X (player 0)
    # must play 2 or lose next turn. Leaving it open loses most rollouts.
    state = {"board": [2, 2, 0, 1, 0, 0, 1, 0, 0]}
    legal = game.legal_moves(state, 0)
    for seed in range(12):
        assert MctsAgent(seed=seed).select_move(game, state, 0, legal) == 2


class _MateInTwoGame:
    """
    The mover wins only by looking past the immediate reply:

      step 0 (mover):    "setup" -> step 1 (live) ; "draw" -> terminal draw
      step 1 (opponent): "forced" -> step 2 (live)        [only legal move]
      step 2 (mover):    "finish" -> terminal, mover wins  [only legal move]

    Every line under "setup" is forced and ends in a win, so all rollouts there
    return the mover as winner; MCTS must prefer "setup" over the drawing move.
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


def test_finds_forced_win_over_a_draw() -> None:
    game = _MateInTwoGame()
    state = game.initial_state()
    legal = ["setup", "draw"]
    for seed in range(12):
        assert MctsAgent(seed=seed).select_move(game, state, 0, legal) == "setup"


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
        # "starve" leaves the opponent with no reply (an arena forfeit -> a win),
        # which must beat the drawing "pass".
        assert MctsAgent(seed=seed).select_move(game, state, 0, ["starve", "pass"]) == "starve"


# ---------------------------------------------------------------------------
# Strength on tic-tac-toe (small enough that informed rollouts play it well)
# ---------------------------------------------------------------------------


def _play_many(make0, make1, n: int) -> list[int]:
    """Return [p0_wins, p1_wins, draws] over n tic-tac-toe matches."""
    tally = [0, 0, 0]
    for i in range(n):
        res = play_match(TicTacToe(), make0(i), make1(i))
        tally[2 if res.winner is None else res.winner] += 1
    return tally


def test_never_loses_tictactoe_vs_random() -> None:
    # Random play cannot punish MCTS's rare imperfections, so it should never
    # lose from either seat.
    as_p0 = _play_many(lambda i: MctsAgent(seed=i), lambda i: RandomAgent(seed=1000 + i), 20)
    assert as_p0[1] == 0, f"mcts lost as p0: {as_p0}"
    as_p1 = _play_many(lambda i: RandomAgent(seed=1000 + i), lambda i: MctsAgent(seed=i), 20)
    assert as_p1[0] == 0, f"mcts lost as p1: {as_p1}"


def test_beats_random_decisively_on_tictactoe() -> None:
    as_p0 = _play_many(lambda i: MctsAgent(seed=i), lambda i: RandomAgent(seed=1000 + i), 20)
    # A search-guided player crushes uniform-random tic-tac-toe; most games are wins.
    assert as_p0[0] >= 15, f"mcts did not dominate random: {as_p0}"


def test_two_mcts_mostly_draw_tictactoe() -> None:
    # Two strong players draw optimal tic-tac-toe; allow a few stochastic
    # decisive games (random rollouts are not a perfect solver).
    res = _play_many(lambda i: MctsAgent(seed=i), lambda i: MctsAgent(seed=500 + i), 20)
    assert res[2] >= 16, f"two MCTS agents should mostly draw, got {res}"


def test_beats_greedy_on_tictactoe() -> None:
    as_p0 = _play_many(lambda i: MctsAgent(seed=i), lambda i: GreedyAgent(seed=1000 + i), 12)
    as_p1 = _play_many(lambda i: GreedyAgent(seed=1000 + i), lambda i: MctsAgent(seed=i), 12)
    mcts_losses = as_p0[1] + as_p1[0]
    mcts_wins = as_p0[0] + as_p1[1]
    # Deeper-looking search converts greedy's 1-ply blunders into wins and is
    # never worse on balance.
    assert mcts_wins > mcts_losses, f"mcts not ahead of greedy: p0={as_p0} p1={as_p1}"


# ---------------------------------------------------------------------------
# Invariants: always legal, deterministic under a seed, never raises, bounded
# ---------------------------------------------------------------------------


def test_always_returns_a_legal_move() -> None:
    game = TicTacToe()
    rng = random.Random(123)
    # The legal-move invariant is independent of the budget, so use a small one
    # to keep the position walk fast.
    agent = MctsAgent(seed=7, iterations=80, node_budget=1500)
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
    a = MctsAgent(seed=42).select_move(game, state, 0, legal)
    b = MctsAgent(seed=42).select_move(game, state, 0, legal)
    assert a == b


def test_different_seeds_can_differ() -> None:
    # When every move is genuinely equivalent (here: all lead to an immediate
    # draw), the agent has no real preference and the seeded shuffle/tie-break
    # decides -- which must vary by seed. Unlike the open tic-tac-toe board,
    # where strong play rationally converges on the centre, this indifference is
    # structural, so the diversity does not depend on a lucky seed window.
    class _AllDrawGame:
        name = "alldraw"

        def initial_state(self):
            return {"d": 0}

        def legal_moves(self, state, player):
            return ["a", "b", "c", "d", "e"] if state["d"] == 0 else []

        def apply_move(self, state, player, move):
            return {"d": 1}

        def terminal(self, state):
            return Terminal(True, None, "draw") if state["d"] >= 1 else Terminal(False, None, "")

        def render(self, state):
            return str(state)

    game = _AllDrawGame()
    legal = ["a", "b", "c", "d", "e"]
    picks = {MctsAgent(seed=s).select_move(game, {"d": 0}, 0, legal) for s in range(16)}
    assert len(picks) > 1


def test_single_legal_move_is_returned_without_search() -> None:
    calls: list[str] = []

    class _OneMoveGame:
        name = "one"

        def initial_state(self):
            return {}

        def legal_moves(self, state, player):
            calls.append("legal_moves")
            return ["x"]

        def apply_move(self, state, player, move):
            calls.append("apply_move")
            return {}

        def terminal(self, state):
            calls.append("terminal")
            return Terminal(False, None, "")

        def render(self, state):
            return ""

    # A lone legal move short-circuits before ANY speculative game call. (A
    # raise here would be swallowed by the misbehaving-game guards, so record
    # calls instead: removing the short-circuit makes ``calls`` non-empty.)
    assert MctsAgent(seed=0).select_move(_OneMoveGame(), {}, 0, ["only"]) == "only"
    assert calls == [], f"expected no game calls for a single move, got {calls}"


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

    move = MctsAgent(seed=1).select_move(_ExplodingGame(), {}, 0, ["a", "b", "c"])
    assert move in {"a", "b", "c"}  # falls back to a legal move, no exception


def test_inner_speculation_errors_are_swallowed() -> None:
    class _FlakyGame:
        """Root moves apply cleanly; deeper speculative calls then raise,
        exercising the guards in expansion and rollout."""

        name = "flaky"

        def __init__(self, fail: str) -> None:
            self.fail = fail  # "terminal" | "legal" | "apply"

        def initial_state(self):
            return {"d": 0}

        def legal_moves(self, state, player):
            if state["d"] == 0:
                return ["go", "go2"]
            if self.fail == "legal":
                raise RuntimeError("legal boom")
            return ["a", "b"]

        def apply_move(self, state, player, move):
            if state["d"] == 0:
                return {"d": 1}  # root moves: always clean
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
        move = MctsAgent(seed=0).select_move(game, {"d": 0}, 0, ["go", "go2"])
        assert move in {"go", "go2"}, f"mode {fail!r} should still return a legal move"


def test_respects_node_budget() -> None:
    # The per-turn apply_move count (tree expansion + rollouts) must not exceed
    # node_budget, no matter how many iterations are requested. Count the calls
    # directly rather than trust the internal counter.
    from ai_arena.loading import load_symbol

    inner = load_symbol("opus/game/game.py:OpusGame")()
    calls = 0

    class _Counted:
        name = inner.name

        def initial_state(self):
            return inner.initial_state()

        def legal_moves(self, state, player):
            return inner.legal_moves(state, player)

        def apply_move(self, state, player, move):
            nonlocal calls
            calls += 1
            return inner.apply_move(state, player, move)

        def terminal(self, state):
            return inner.terminal(state)

        def render(self, state):
            return inner.render(state)

    game = _Counted()
    state = game.initial_state()
    legal = game.legal_moves(state, 0)
    # 10k iterations would expand far past 300 apply_move calls if the budget
    # were ignored; a budget-respecting agent stops at the cap.
    agent = MctsAgent(seed=0, iterations=10_000, node_budget=300)
    move = agent.select_move(game, state, 0, legal)
    assert move in legal
    assert calls <= agent.node_budget, f"exceeded node_budget: {calls} apply_move calls"
    assert calls > agent.node_budget // 10, f"budget barely used ({calls}); not actually searching"
    assert 0 <= agent._budget <= agent.node_budget  # internal counter never underflows


# ---------------------------------------------------------------------------
# End-to-end: clean play, and generality across a competitor game
# ---------------------------------------------------------------------------


def test_mcts_vs_random_completes_cleanly() -> None:
    res = play_match(TicTacToe(), MctsAgent(seed=1), RandomAgent(seed=2))
    assert res.reason in {"win", "draw"}
    assert all(r.note is None for r in res.move_history)  # mcts never forfeits


def test_mcts_is_game_agnostic_on_caldera() -> None:
    # No Caldera-specific code in MctsAgent: it drives a full match purely
    # through the Game protocol and never forfeits. A modest budget keeps the
    # match fast; generality, not strength, is the point here.
    from opus.game.game import OpusGame

    res = play_match(OpusGame(), MctsAgent(seed=3, node_budget=4000), RandomAgent(seed=4))
    assert res.turns >= 1
    assert res.reason
    assert all(r.note is None for r in res.move_history)
