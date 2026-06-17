from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..game import Game, PlayerId
from ..json_types import JSONValue

# Terminal outcomes dominate every heuristic score. The ``+ depth`` term (depth
# is the remaining search depth, so it is larger for shallower nodes) makes the
# agent prefer winning sooner and losing later, all else equal.
_WIN = 1_000_000

# Default ceiling on game-state expansions (apply_move calls) per turn. Like
# GreedyAgent.safety_budget, this bounds worst-case cost on a high-branching
# game; the search returns the best move found so far when it runs out. The
# default comfortably covers the neutral tic-tac-toe game, whose alpha-beta
# search costs ~21k node expansions from the opening (its worst case -- the full
# unpruned tree is far larger, but pruning never needs more than that here), so
# the agent never truncates and plays tic-tac-toe perfectly.
_DEFAULT_NODE_BUDGET = 40_000

# Default search horizon in plies. Large enough to solve small games outright
# (tic-tac-toe ends within 9 plies); on the arena's deeper games the node
# budget, not this depth, is the binding limit.
_DEFAULT_MAX_DEPTH = 12


@dataclass(slots=True)
class SearchAgent:
    """
    A game-agnostic depth-limited negamax (alpha-beta) baseline, stronger than
    GreedyAgent.

    Using only the Game protocol (``legal_moves`` / ``apply_move`` /
    ``terminal``), it searches the game tree up to ``max_depth`` plies and scores
    leaves purely by terminal outcome:

      - a win for the side to move scores ``+(WIN + depth)``;
      - a loss scores ``-(WIN + depth)`` (this also covers a position with no
        legal moves, which the arena scores as a loss for the player to move);
      - a draw scores ``0``;
      - a non-terminal position at the depth horizon scores ``0`` (unknown).

    With no domain heuristic, the horizon score is neutral, so the agent plays
    perfectly on games small enough to search to their end (it never loses
    tic-tac-toe) and behaves like a deeper-horizon GreedyAgent on larger games
    -- it still grabs forced wins and dodges forced losses, just several plies
    further ahead.

    Guarantees (given the engine's contract that ``legal_moves`` is non-empty):
      - The returned move is always an element of ``legal_moves``, so the engine
        can never flag it illegal.
      - Exceptions from speculative game calls are swallowed: a child whose
        ``apply_move`` raises is skipped, and a node whose ``legal_moves`` /
        ``terminal`` raises scores as unknown, so a misbehaving game cannot make
        this agent forfeit a turn it could otherwise play.

    It relies on the documented contract that ``apply_move`` returns a new state
    without mutating its input. With a fixed ``seed`` the agent is fully
    deterministic: equal-valued moves are broken by a seeded shuffle.
    """

    name: str = "search"
    seed: int | None = None
    max_depth: int = _DEFAULT_MAX_DEPTH
    node_budget: int = _DEFAULT_NODE_BUDGET
    _rng: random.Random = field(init=False, repr=False)
    _nodes: int = field(init=False, repr=False, default=0)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def select_move(
        self,
        game: Game,
        state: JSONValue,
        player: PlayerId,
        legal_moves: list[JSONValue],
    ) -> JSONValue:
        # Shuffle so equal-valued moves are chosen uniformly (and reproducibly
        # under a seed) instead of always favouring the generator's ordering.
        moves = list(legal_moves)
        self._rng.shuffle(moves)

        self._nodes = 0
        depth = max(1, int(self.max_depth))

        best_move = moves[0]
        best_val = float("-inf")
        alpha = float("-inf")
        beta = float("inf")
        opponent: PlayerId = 1 - player

        for m in moves:
            try:
                child = game.apply_move(state, player, m)
            except Exception:
                continue  # unevaluable; keep as fallback only
            val = -self._negamax(game, child, opponent, depth - 1, -beta, -alpha)
            if val > best_val:
                best_val = val
                best_move = m
            if best_val > alpha:
                alpha = best_val
            # No pruning across root moves (beta stays +inf): when the search
            # finishes within budget, best_move is the exact argmax and the
            # seeded shuffle breaks ties deterministically. If the node budget is
            # exhausted first, deeper lines collapse to a neutral score and the
            # choice degrades to best-effort (like greedy's safety budget) --
            # never illegal, but possibly missing a win beyond the cutoff.

        return best_move

    def _negamax(
        self,
        game: Game,
        state: JSONValue,
        player: PlayerId,
        depth: int,
        alpha: float,
        beta: float,
    ) -> float:
        """Value of ``state`` for ``player`` (to move), via fail-soft negamax."""
        try:
            t = game.terminal(state)
        except Exception:
            return 0.0
        if t.is_terminal:
            if t.winner is None:
                return 0.0
            return float(_WIN + depth) if t.winner == player else float(-(_WIN + depth))

        if depth <= 0 or self._nodes >= self.node_budget:
            return 0.0  # horizon / budget: unknown, treated as neutral

        try:
            legal = game.legal_moves(state, player)
        except Exception:
            return 0.0
        if not legal:
            # The arena forfeits a player with no legal moves: a loss to move.
            return float(-(_WIN + depth))

        best = float("-inf")
        for m in legal:
            if self._nodes >= self.node_budget:
                break
            self._nodes += 1
            try:
                child = game.apply_move(state, player, m)
            except Exception:
                continue
            val = -self._negamax(game, child, 1 - player, depth - 1, -beta, -alpha)
            if val > best:
                best = val
            if best > alpha:
                alpha = best
            if alpha >= beta:
                break  # opponent already has a better line; prune

        if best == float("-inf"):
            # Every child raised (or budget hit before any expanded): unknown.
            return 0.0
        return best
