from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from ai_arena.game import PlayerId, Terminal
from ai_arena.json_types import JSONValue

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SIZE = 7
MAX_PLY = 50
VENT = -1  # Height sentinel for erupted / impassable cells

# Piece type tags
CROWN = "crown"
LANCER = "lancer"
SMITH = "smith"

# Directions
DIRS_8 = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]
DIRS_4 = [(-1, 0), (0, 1), (1, 0), (0, -1)]


# ---------------------------------------------------------------------------
# Game class
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class OpusGame:
    """
    Caldera  --  Opus home game.

    A volcanic tactics game on a 7x7 grid.  Three asymmetric piece types
    (Crown, Lancer, Smith) manoeuvre across terrain that rises, erupts, and
    chain-reacts.

    See rules.md for the full specification.
    """

    name: str = "caldera"

    # ------------------------------------------------------------------
    # Protocol methods
    # ------------------------------------------------------------------

    def initial_state(self) -> JSONValue:
        board = [[0] * SIZE for _ in range(SIZE)]
        return {
            "board": board,
            "p0": [
                {"type": CROWN, "r": 6, "c": 3},
                {"type": LANCER, "r": 6, "c": 1},
                {"type": LANCER, "r": 6, "c": 5},
                {"type": SMITH, "r": 6, "c": 2},
                {"type": SMITH, "r": 6, "c": 4},
            ],
            "p1": [
                {"type": CROWN, "r": 0, "c": 3},
                {"type": LANCER, "r": 0, "c": 1},
                {"type": LANCER, "r": 0, "c": 5},
                {"type": SMITH, "r": 0, "c": 2},
                {"type": SMITH, "r": 0, "c": 4},
            ],
            "ply": 0,
            "winner": None,
            "reason": "",
        }

    def legal_moves(self, state: JSONValue, player: PlayerId) -> list[JSONValue]:
        if state["winner"] is not None or state["ply"] >= MAX_PLY:
            return []

        board: list[list[int]] = state["board"]
        my_pieces: list[dict] = state[f"p{player}"]
        opp_pieces: list[dict] = state[f"p{1 - player}"]

        friendly = {(p["r"], p["c"]) for p in my_pieces}
        enemy = {(p["r"], p["c"]) for p in opp_pieces}

        moves: list[JSONValue] = []

        for piece in my_pieces:
            pr, pc = piece["r"], piece["c"]
            ptype = piece["type"]

            # --- movement (all piece types) ---
            if ptype in (CROWN, SMITH):
                _add_step_moves(moves, board, friendly, pr, pc)

            elif ptype == LANCER:
                # 1-step and 2-step straight-line moves
                for dr, dc in DIRS_8:
                    # --- 1 step ---
                    r1, c1 = pr + dr, pc + dc
                    if _in_bounds(r1, c1) and board[r1][c1] != VENT \
                            and (r1, c1) not in friendly \
                            and _can_climb(board[pr][pc], board[r1][c1]):
                        moves.append({"action": "move", "from": [pr, pc], "to": [r1, c1]})

                    # --- 2 steps (leap) ---
                    r2, c2 = pr + 2 * dr, pc + 2 * dc
                    if _in_bounds(r1, c1) and _in_bounds(r2, c2) \
                            and board[r1][c1] != VENT and board[r2][c2] != VENT \
                            and (r2, c2) not in friendly \
                            and _can_climb(board[pr][pc], board[r1][c1]) \
                            and _can_climb(board[r1][c1], board[r2][c2]):
                        moves.append({"action": "move", "from": [pr, pc], "to": [r2, c2]})

            # --- forge (smiths only) ---
            if ptype == SMITH:
                for dr, dc in DIRS_4:
                    tr, tc = pr + dr, pc + dc
                    if _in_bounds(tr, tc) and board[tr][tc] != VENT \
                            and (tr, tc) not in friendly and (tr, tc) not in enemy:
                        moves.append({"action": "forge", "smith": [pr, pc], "target": [tr, tc]})

        return moves

    def apply_move(self, state: JSONValue, player: PlayerId, move: JSONValue) -> JSONValue:
        # Deep-copy mutable state
        board = [row[:] for row in state["board"]]
        p0 = [dict(p) for p in state["p0"]]
        p1 = [dict(p) for p in state["p1"]]
        ply = state["ply"] + 1

        my_key = f"p{player}"
        opp_key = f"p{1 - player}"
        my = p0 if player == 0 else p1
        opp = p1 if player == 0 else p0

        winner = None
        reason = ""

        if move["action"] == "move":
            fr, fc = move["from"]
            tr, tc = move["to"]

            # Relocate piece
            for p in my:
                if p["r"] == fr and p["c"] == fc:
                    p["r"] = tr
                    p["c"] = tc
                    break

            # Capture
            for i, p in enumerate(opp):
                if p["r"] == tr and p["c"] == tc:
                    captured = opp.pop(i)
                    if captured["type"] == CROWN:
                        winner = player
                        reason = "crown_captured"
                    break

        elif move["action"] == "forge":
            tr, tc = move["target"]
            board[tr][tc] += 1

            if board[tr][tc] >= 4:
                _resolve_eruptions(board, p0, p1)

                c0 = any(p["type"] == CROWN for p in p0)
                c1 = any(p["type"] == CROWN for p in p1)

                if not c0 and not c1:
                    winner = 1 - player
                    reason = "crowns_erupted_mutual"
                elif not c0:
                    winner = 1
                    reason = "crown_erupted"
                elif not c1:
                    winner = 0
                    reason = "crown_erupted"

        new_state: dict[str, JSONValue] = {
            "board": board,
            "p0": p0,
            "p1": p1,
            "ply": ply,
            "winner": winner,
            "reason": reason,
        }
        return new_state

    def terminal(self, state: JSONValue) -> Terminal:
        w = state["winner"]
        if w is not None:
            return Terminal(is_terminal=True, winner=w, reason=str(state["reason"]))

        if state["ply"] >= MAX_PLY:
            w2, r2 = _winner_on_limit(state)
            return Terminal(is_terminal=True, winner=w2, reason=r2)

        return Terminal(is_terminal=False, winner=None, reason="")

    def render(self, state: JSONValue) -> str:
        board: list[list[int]] = state["board"]

        # Build piece map: (r, c) -> display char
        pmap: dict[tuple[int, int], str] = {}
        syms = {CROWN: "c", LANCER: "l", SMITH: "s"}
        for p in state["p0"]:
            pmap[(p["r"], p["c"])] = syms[p["type"]]  # lowercase = P0
        for p in state["p1"]:
            pmap[(p["r"], p["c"])] = syms[p["type"]].upper()  # UPPER = P1

        n0 = len(state["p0"])
        n1 = len(state["p1"])
        lines = [f"--- Caldera  ply={state['ply']}  P0({n0})  P1({n1}) ---"]

        header = "  " + " ".join(str(c) for c in range(SIZE))
        lines.append(header)

        for r in range(SIZE):
            parts = [f"{r}"]
            for c in range(SIZE):
                if (r, c) in pmap:
                    parts.append(pmap[(r, c)])
                elif board[r][c] == VENT:
                    parts.append("X")
                else:
                    parts.append(str(board[r][c]))
            lines.append(" ".join(parts))

        if state["winner"] is not None:
            lines.append(f"Winner: P{state['winner']} ({state['reason']})")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _in_bounds(r: int, c: int) -> bool:
    return 0 <= r < SIZE and 0 <= c < SIZE


def _can_climb(from_h: int, to_h: int) -> bool:
    """Ascending at most 1 level; descending any amount."""
    return to_h <= from_h + 1


def _add_step_moves(
    moves: list[JSONValue],
    board: list[list[int]],
    friendly: set[tuple[int, int]],
    pr: int,
    pc: int,
) -> None:
    """Append single-step moves (8-dir) for Crown / Smith."""
    for dr, dc in DIRS_8:
        tr, tc = pr + dr, pc + dc
        if _in_bounds(tr, tc) and board[tr][tc] != VENT \
                and (tr, tc) not in friendly \
                and _can_climb(board[pr][pc], board[tr][tc]):
            moves.append({"action": "move", "from": [pr, pc], "to": [tr, tc]})


def _resolve_eruptions(
    board: list[list[int]],
    p0: list[dict],
    p1: list[dict],
) -> None:
    """Process all pending eruptions (cells at height >= 4) via BFS.

    Operates on the already-copied board/piece lists from apply_move.
    """
    queue: deque[tuple[int, int]] = deque()

    # Seed: every cell currently >= 4
    for r in range(SIZE):
        for c in range(SIZE):
            if board[r][c] >= 4:
                queue.append((r, c))

    while queue:
        er, ec = queue.popleft()
        if board[er][ec] == VENT:
            continue  # already erupted on an earlier step
        if board[er][ec] < 4:
            continue  # height dropped back (shouldn't happen, but safety check)

        # Erupt this cell
        board[er][ec] = VENT

        # Destroy any piece here
        for pieces in (p0, p1):
            pieces[:] = [p for p in pieces if not (p["r"] == er and p["c"] == ec)]

        # Raise orthogonal neighbours
        for dr, dc in DIRS_4:
            nr, nc = er + dr, ec + dc
            if _in_bounds(nr, nc) and board[nr][nc] != VENT:
                board[nr][nc] += 1
                if board[nr][nc] >= 4:
                    queue.append((nr, nc))


def _winner_on_limit(state: JSONValue) -> tuple[int | None, str]:
    """Determine winner when ply limit reached."""
    n0 = len(state["p0"])
    n1 = len(state["p1"])

    if n0 != n1:
        return (0 if n0 > n1 else 1), "turn_limit_pieces"

    # Tie-break: Crown height
    def _crown_h(pieces: list[dict], board: list[list[int]]) -> int:
        for p in pieces:
            if p["type"] == CROWN:
                h = board[p["r"]][p["c"]]
                return h if h != VENT else 0
        return 0

    h0 = _crown_h(state["p0"], state["board"])
    h1 = _crown_h(state["p1"], state["board"])

    if h0 != h1:
        return (0 if h0 > h1 else 1), "turn_limit_crown_height"

    return None, "turn_limit_draw"
