from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..game import Game, PlayerId
from ..json_types import JSONValue

# Default ceiling on speculative opponent-reply evaluations per turn. The
# loss-avoidance scan is O(branching^2); on the arena's small boards it finishes
# well within this, but the cap bounds worst-case cost on a high-branching game.
# A candidate not fully verified within budget is reported as "unknown" -- it is
# never silently assumed safe.
_DEFAULT_SAFETY_BUDGET = 12_000


@dataclass(slots=True)
class GreedyAgent:
    """
    A general, game-agnostic baseline that is stronger than RandomAgent.

    Using only the Game protocol (apply_move / terminal / legal_moves), each turn
    it:

      1. plays an immediately winning move if one exists;
      2. otherwise avoids moves that let the opponent win on their next turn --
         which also avoids self-destructive moves (a move that ends the game in
         the opponent's favour the instant it is applied, e.g. a mutual-loss
         eruption or firing a laser into one's own king);
      3. otherwise falls back to a random legal move.

    It is intentionally shallow (1 ply for offence, 1 ply for defence) so it
    stays cheap and game-agnostic; it is not a strong player, just a sensible
    skill floor for benchmarking real agents.

    Guarantees (given the engine's contract that ``legal_moves`` is non-empty):
      - The returned move is always an element of ``legal_moves``, so the engine
        can never flag it illegal.
      - Exceptions from speculative game calls are swallowed (treated
        conservatively: that candidate is left unproven, never preferred over a
        proven-safe one), so a misbehaving game cannot make this agent forfeit a
        turn it could otherwise play.

    It relies on the documented contract that ``apply_move`` returns a new state
    without mutating its input -- the engine and replay rely on this too. With a
    fixed ``seed`` the agent is fully deterministic.
    """

    name: str = "greedy"
    seed: int | None = None
    safety_budget: int = _DEFAULT_SAFETY_BUDGET
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def select_move(
        self,
        game: Game,
        state: JSONValue,
        player: PlayerId,
        legal_moves: list[JSONValue],
    ) -> JSONValue:
        # Shuffle so ties are broken uniformly (and reproducibly under a seed)
        # rather than always favouring the move-generator's ordering.
        moves = list(legal_moves)
        self._rng.shuffle(moves)
        opponent: PlayerId = 1 - player

        # Phase 1 -- take an immediate win.
        for m in moves:
            try:
                t = game.terminal(game.apply_move(state, player, m))
            except Exception:
                continue
            if t.is_terminal and t.winner == player:
                return m

        # Phase 2 -- classify the rest by whether they hand the opponent an
        # immediate win (or lose on the spot). Prefer proven-safe moves, then
        # unverified ones, then known-bad ones; within each pool keep the
        # shuffled order so the choice stays uniform.
        safe: list[JSONValue] = []
        unknown: list[JSONValue] = []
        unsafe: list[JSONValue] = []
        budget = self.safety_budget

        for m in moves:
            verdict, budget = self._classify(game, state, player, opponent, m, budget)
            if verdict == "safe":
                safe.append(m)
            elif verdict == "unsafe":
                unsafe.append(m)
            else:
                unknown.append(m)

        pool = safe or unknown or unsafe or moves
        return pool[0]

    def _classify(
        self,
        game: Game,
        state: JSONValue,
        player: PlayerId,
        opponent: PlayerId,
        move: JSONValue,
        budget: int,
    ) -> tuple[str, int]:
        """Return ``("safe" | "unsafe" | "unknown", remaining_budget)`` for ``move``."""
        try:
            nxt = game.apply_move(state, player, move)
            t = game.terminal(nxt)
        except Exception:
            return "unknown", budget

        if t.is_terminal:
            # Outright wins were already returned in phase 1, so a terminal here
            # is either an opponent win (self-loss to avoid) or a draw (fine).
            return ("unsafe" if t.winner == opponent else "safe"), budget

        try:
            replies = game.legal_moves(nxt, opponent)
        except Exception:
            return "unknown", budget
        if not replies:
            return "safe", budget  # opponent has no reply -> good for us

        for om in replies:
            if budget <= 0:
                return "unknown", budget  # ran out before proving safety
            budget -= 1
            try:
                t2 = game.terminal(game.apply_move(nxt, opponent, om))
            except Exception:
                continue
            if t2.is_terminal and t2.winner == opponent:
                return "unsafe", budget

        return "safe", budget
