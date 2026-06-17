from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from ..game import Game, PlayerId
from ..json_types import JSONValue

# Default number of MCTS simulations (selection -> expansion -> rollout ->
# backprop) per turn. On a small game this is the binding limit and a few
# hundred playouts already give strong play; on the arena's high-branching
# games the node budget below usually binds first.
_DEFAULT_ITERATIONS = 800

# Default hard ceiling on game-state expansions (apply_move calls) per turn,
# across both tree growth and rollouts. Like SearchAgent.node_budget and
# GreedyAgent.safety_budget, this bounds worst-case per-turn cost on a
# high-branching game regardless of the iteration count: when it runs out, the
# agent stops simulating and returns the best move found so far.
_DEFAULT_NODE_BUDGET = 50_000

# Default cap on the length of a single random rollout. A rollout that reaches
# this many plies without a terminal scores as a draw (neutral, unknown), so a
# game that rarely ends on its own cannot make one playout run unbounded.
_DEFAULT_ROLLOUT_DEPTH = 60

# UCT exploration constant (~sqrt(2)); higher explores more, lower exploits more.
_DEFAULT_EXPLORATION = 1.4


@dataclass(slots=True)
class _Node:
    """One game state in the search tree.

    ``value`` is accumulated from the perspective of ``to_move`` (the player
    about to move at this node), so the negamax UCT formula in
    ``_best_uct_child`` negates a child's mean value to score it for the
    *parent's* mover.
    """

    state: JSONValue
    to_move: PlayerId
    move: JSONValue  # the move that created this node (unused at the root)
    is_terminal: bool
    winner: PlayerId | None  # meaningful only when is_terminal
    moves: list[JSONValue]  # legal moves (shuffled); empty when terminal
    parent: "_Node | None"
    untried: list[int]  # indices into ``moves`` not yet expanded
    children: dict[int, "_Node"] = field(default_factory=dict)
    visits: int = 0
    value: float = 0.0


@dataclass(slots=True)
class MctsAgent:
    """
    A game-agnostic Monte Carlo Tree Search baseline, stronger than SearchAgent
    on the arena's larger games.

    Using only the Game protocol (``legal_moves`` / ``apply_move`` /
    ``terminal``), it grows an asymmetric search tree by repeatedly: walking
    down the tree by the UCT rule, expanding one new child, playing a uniformly
    random rollout from it to a terminal (or the rollout-depth horizon), and
    backing the result up the path. The move played is the most-visited child of
    the root (the robust choice).

    Where SearchAgent has no domain heuristic and so scores everything beyond
    its horizon as a draw -- which makes it play shallowly on a high-branching
    game it cannot search to the end -- MCTS substitutes random playouts for that
    missing heuristic. It therefore keeps improving with more simulations on the
    big arena games (Caldera, Photon, Skysummit), while still drawing perfect
    games like tic-tac-toe given enough playouts.

    The agent assumes the arena's strictly alternating turn order (the engine
    advances ``player`` to ``1 - player`` after every applied move), so it tracks
    whose turn it is by alternating from the root player.

    Guarantees (given the engine's contract that ``legal_moves`` is non-empty):
      - The returned move is always an element of ``legal_moves``, so the engine
        can never flag it illegal.
      - Exceptions from speculative game calls are swallowed (a move whose
        ``apply_move`` raises is skipped; a rollout that hits a raising
        ``terminal`` / ``legal_moves`` ends as a neutral draw), so a misbehaving
        game cannot make this agent forfeit a turn it could otherwise play.
      - A player left with no legal moves is scored as a loss for that player,
        matching the engine's "no_legal_moves" forfeit rule.

    It relies on the documented contract that ``apply_move`` returns a new state
    without mutating its input. With a fixed ``seed`` the agent is fully
    deterministic.
    """

    name: str = "mcts"
    seed: int | None = None
    iterations: int = _DEFAULT_ITERATIONS
    node_budget: int = _DEFAULT_NODE_BUDGET
    rollout_depth: int = _DEFAULT_ROLLOUT_DEPTH
    exploration: float = _DEFAULT_EXPLORATION
    _rng: random.Random = field(init=False, repr=False)
    _budget: int = field(init=False, repr=False, default=0)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def select_move(
        self,
        game: Game,
        state: JSONValue,
        player: PlayerId,
        legal_moves: list[JSONValue],
    ) -> JSONValue:
        root = self._make_node(game, state, player, None, root_moves=legal_moves)
        # With a single option there is nothing to search; this also keeps the
        # tic-tac-toe-style forced lines cheap.
        if len(root.moves) == 1:
            return root.moves[0]

        self._budget = self.node_budget
        for _ in range(max(0, int(self.iterations))):
            if self._budget <= 0:
                break
            leaf = self._tree_policy(game, root)
            winner = self._rollout(game, leaf)
            self._backprop(leaf, winner)

        # Robust choice: the most-visited child. ``moves`` is shuffled, so the
        # max() tie-break is uniform-but-reproducible under the seed.
        best: _Node | None = None
        for child in root.children.values():
            if best is None or child.visits > best.visits:
                best = child
        # No child was ever expanded (e.g. node_budget == 0): any legal move.
        return best.move if best is not None else root.moves[0]

    # ------------------------------------------------------------------
    # MCTS phases
    # ------------------------------------------------------------------

    def _tree_policy(self, game: Game, node: _Node) -> _Node:
        """Descend to a leaf, expanding the first node with an untried move."""
        while not node.is_terminal:
            if node.untried:
                return self._expand(game, node)
            child = self._best_uct_child(node)
            if child is None:
                break  # no expandable children (all speculative applies failed)
            node = child
        return node

    def _expand(self, game: Game, node: _Node) -> _Node:
        """Add and return one child of ``node`` (skipping moves that raise)."""
        while node.untried:
            idx = node.untried.pop()
            move = node.moves[idx]
            if self._budget <= 0:
                break
            self._budget -= 1
            try:
                child_state = game.apply_move(node.state, node.to_move, move)
            except Exception:
                continue  # unevaluable move; drop it from this node
            child = self._make_node(game, child_state, 1 - node.to_move, node, move=move)
            node.children[idx] = child
            return child
        # Every remaining move was unexpandable: fall back to an existing child,
        # or this node itself (rolled out in place) when there is none.
        return self._best_uct_child(node) or node

    def _best_uct_child(self, node: _Node) -> _Node | None:
        if not node.children:
            return None
        log_n = math.log(node.visits) if node.visits > 0 else 0.0
        best: _Node | None = None
        best_score = float("-inf")
        for child in node.children.values():
            if child.visits == 0:
                return child  # always sample an unvisited child first
            # child.value is from the child mover's perspective; negate it to
            # score the child for the player choosing at ``node`` (negamax).
            exploit = -child.value / child.visits
            explore = self.exploration * math.sqrt(log_n / child.visits)
            score = exploit + explore
            if score > best_score:
                best_score = score
                best = child
        return best

    def _rollout(self, game: Game, node: _Node) -> PlayerId | None:
        """Random playout from ``node``; returns the winner (None == draw)."""
        if node.is_terminal:
            return node.winner

        state = node.state
        to_move = node.to_move
        for _ in range(max(0, int(self.rollout_depth))):
            if self._budget <= 0:
                return None  # budget exhausted: score as a neutral draw
            try:
                t = game.terminal(state)
            except Exception:
                return None
            if t.is_terminal:
                return t.winner
            try:
                legal = game.legal_moves(state, to_move)
            except Exception:
                return None
            if not legal:
                return 1 - to_move  # no legal moves: an arena loss for to_move
            move = self._rng.choice(legal)
            self._budget -= 1
            try:
                state = game.apply_move(state, to_move, move)
            except Exception:
                return None
            to_move = 1 - to_move
        return None  # rollout-depth horizon reached: unknown, treated as a draw

    def _backprop(self, node: _Node | None, winner: PlayerId | None) -> None:
        while node is not None:
            node.visits += 1
            if winner is not None:
                # +1 for the player to move at this node when they win the
                # rollout, -1 when they lose; draws contribute 0.
                node.value += 1.0 if winner == node.to_move else -1.0
            node = node.parent

    # ------------------------------------------------------------------
    # Node construction
    # ------------------------------------------------------------------

    def _make_node(
        self,
        game: Game,
        state: JSONValue,
        to_move: PlayerId,
        parent: _Node | None,
        *,
        move: JSONValue = None,
        root_moves: list[JSONValue] | None = None,
    ) -> _Node:
        is_terminal, winner, moves = self._classify(game, state, to_move, root_moves)
        # Shuffle so expansion order and the most-visited tie-break are uniform
        # (and reproducible under a seed) rather than biased by move generation.
        self._rng.shuffle(moves)
        return _Node(
            state=state,
            to_move=to_move,
            move=move,
            is_terminal=is_terminal,
            winner=winner,
            moves=moves,
            parent=parent,
            untried=list(range(len(moves))),
        )

    def _classify(
        self,
        game: Game,
        state: JSONValue,
        to_move: PlayerId,
        root_moves: list[JSONValue] | None,
    ) -> tuple[bool, PlayerId | None, list[JSONValue]]:
        """Return ``(is_terminal, winner, legal_moves)`` for a state.

        A non-terminal state with no legal moves is reported as terminal with
        the mover as the loser, matching the engine's forfeit rule. The root is
        always built from the engine-supplied ``root_moves`` (never terminal),
        so a flaky ``terminal`` cannot make the agent abandon a real turn.
        """
        if root_moves is None:
            try:
                t = game.terminal(state)
            except Exception:
                t = None
            if t is not None and t.is_terminal:
                return True, t.winner, []

        if root_moves is not None:
            moves = list(root_moves)
        else:
            try:
                moves = list(game.legal_moves(state, to_move))
            except Exception:
                moves = []

        if not moves:
            return True, 1 - to_move, []
        return False, None, moves
