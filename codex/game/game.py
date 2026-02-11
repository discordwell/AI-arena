from __future__ import annotations

from dataclasses import dataclass

from ai_arena.game import PlayerId, Terminal
from ai_arena.json_types import JSONValue


@dataclass(slots=True)
class CodexGame:
    """
    Skysummit (Codex home game)

    A deterministic, perfect-information, 2-player climbing/building duel inspired by
    "move + build" tower games.

    See GAME.md for the full rules.
    """

    name: str = "skysummit"

    board_size: int = 5
    max_ply: int = 200  # hard stop; tie-break by altitude score

    def initial_state(self) -> JSONValue:
        n = self.board_size * self.board_size
        return {
            "phase": "place",  # "place" -> "play" -> terminal via winner/turn limit
            "ply": 0,
            "board": [0] * n,  # heights: 0..3, 4 == dome (blocked)
            "workers": [[None, None], [None, None]],  # per-player, two workers each
            "winner": None,
            "reason": "",
        }

    def legal_moves(self, state: JSONValue, player: PlayerId) -> list[JSONValue]:
        s = _as_state(state)
        if s["winner"] is not None:
            return []

        if s["phase"] == "place":
            return self._legal_place_moves(s, player)
        if s["phase"] == "play":
            return self._legal_play_moves(s, player)
        raise ValueError(f"unknown phase: {s['phase']!r}")

    def apply_move(self, state: JSONValue, player: PlayerId, move: JSONValue) -> JSONValue:
        s = _as_state(state)
        if s["winner"] is not None:
            raise ValueError("game is already over")

        if s["phase"] == "place":
            return self._apply_place(s, player, move)
        if s["phase"] == "play":
            return self._apply_play(s, player, move)
        raise ValueError(f"unknown phase: {s['phase']!r}")

    def terminal(self, state: JSONValue) -> Terminal:
        s = _as_state(state)

        winner = s["winner"]
        if winner is not None:
            return Terminal(is_terminal=True, winner=winner, reason=str(s.get("reason", "win")))

        if int(s["ply"]) >= int(self.max_ply):
            w = _winner_on_turn_limit(s)
            return Terminal(is_terminal=True, winner=w, reason="turn_limit")

        return Terminal(is_terminal=False, winner=None, reason="")

    def render(self, state: JSONValue) -> str:
        s = _as_state(state)
        b = _as_board(s["board"])
        w = _as_workers(s["workers"])

        occ: dict[int, str] = {}
        for pid, tag0, tag1 in ((0, "A", "B"), (1, "a", "b")):
            pws = w[pid]
            if isinstance(pws[0], int):
                occ[pws[0]] = tag0
            if isinstance(pws[1], int):
                occ[pws[1]] = tag1

        def hch(v: int) -> str:
            return "D" if v >= 4 else str(v)

        rows: list[str] = []
        n = self.board_size
        for r in range(n):
            parts: list[str] = []
            for c in range(n):
                i = r * n + c
                parts.append(f"{hch(b[i])}{occ.get(i, '.')}")
            rows.append(" ".join(parts))

        header = f"phase={s['phase']} ply={s['ply']}"
        if s["winner"] is not None:
            header += f" winner={s['winner']} reason={s.get('reason', '')}"
        return header + "\n" + "\n".join(rows)

    # --- placement ---

    def _legal_place_moves(self, s: dict[str, JSONValue], player: PlayerId) -> list[JSONValue]:
        w = _as_workers(s["workers"])
        # Placement is 2 turns total: P0 places both workers, then P1 places both.
        if w[player][0] is not None or w[player][1] is not None:
            return []

        taken = _occupied_positions(w)
        empties = [i for i in range(self.board_size * self.board_size) if i not in taken]
        moves: list[JSONValue] = []
        for idx_a, a in enumerate(empties):
            for b in empties[idx_a + 1 :]:
                # Allow either ordering to avoid "unordered pair" footguns for agents.
                moves.append({"t": "place", "to": [a, b]})
                moves.append({"t": "place", "to": [b, a]})
        return moves

    def _apply_place(self, s: dict[str, JSONValue], player: PlayerId, move: JSONValue) -> JSONValue:
        if not isinstance(move, dict) or move.get("t") != "place":
            raise ValueError(f"expected place move, got: {move!r}")
        to = move.get("to")
        if not (isinstance(to, list) and len(to) == 2 and all(isinstance(x, int) for x in to)):
            raise ValueError(f"place.to must be [int,int], got: {to!r}")
        a, b = int(to[0]), int(to[1])
        if a == b:
            raise ValueError("place positions must be distinct")

        w = _as_workers(s["workers"])
        if w[player][0] is not None or w[player][1] is not None:
            raise ValueError("this player has already placed workers")

        n = self.board_size * self.board_size
        if not (0 <= a < n and 0 <= b < n):
            raise ValueError("place out of bounds")

        taken = _occupied_positions(w)
        if a in taken or b in taken:
            raise ValueError("place on occupied cell")

        w2 = [[w[0][0], w[0][1]], [w[1][0], w[1][1]]]
        a2, b2 = (a, b) if a < b else (b, a)
        w2[player] = [a2, b2]

        phase = "play" if all(v is not None for p in w2 for v in p) else "place"
        return {
            "phase": phase,
            "ply": int(s["ply"]) + 1,
            "board": list(_as_board(s["board"])),
            "workers": w2,
            "winner": None,
            "reason": "",
        }

    # --- main play ---

    def _legal_play_moves(self, s: dict[str, JSONValue], player: PlayerId) -> list[JSONValue]:
        b = _as_board(s["board"])
        w = _as_workers(s["workers"])
        if w[player][0] is None or w[player][1] is None:
            # Not ready (shouldn't happen if phase is correct), but avoid crashes.
            return []

        occ_now = _occupied_positions(w)
        moves: list[JSONValue] = []
        for wi in (0, 1):
            src = int(w[player][wi])  # type: ignore[arg-type]
            src_h = b[src]
            for dst in _neighbors(self.board_size, src):
                if dst in occ_now:
                    continue
                dst_h = b[dst]
                if dst_h >= 4:
                    continue  # dome
                if dst_h - src_h > 1:
                    continue  # too steep

                # Winning move: stepping onto height 3 ends the game; building is optional/omitted.
                if dst_h == 3:
                    moves.append({"t": "move", "w": wi, "to": dst, "build": None})
                    continue

                # Otherwise: must build adjacent to the moved worker.
                occ_after = set(occ_now)
                occ_after.remove(src)
                occ_after.add(dst)

                for build in _neighbors(self.board_size, dst):
                    if build in occ_after:
                        continue
                    if b[build] >= 4:
                        continue
                    moves.append({"t": "move", "w": wi, "to": dst, "build": build})
        return moves

    def _apply_play(self, s: dict[str, JSONValue], player: PlayerId, move: JSONValue) -> JSONValue:
        if not isinstance(move, dict) or move.get("t") != "move":
            raise ValueError(f"expected move, got: {move!r}")

        widx = move.get("w")
        to = move.get("to")
        build = move.get("build")
        if not (isinstance(widx, int) and widx in (0, 1)):
            raise ValueError(f"move.w must be 0 or 1, got: {widx!r}")
        if not isinstance(to, int):
            raise ValueError(f"move.to must be int, got: {to!r}")
        if build is not None and not isinstance(build, int):
            raise ValueError(f"move.build must be int or None, got: {build!r}")

        b = list(_as_board(s["board"]))
        w = _as_workers(s["workers"])
        if any(not isinstance(w[pid][wi], int) for pid in (0, 1) for wi in (0, 1)):
            raise ValueError("cannot move before both players have placed")

        src = w[player][widx]
        if not isinstance(src, int):
            raise ValueError("worker position missing")

        n = self.board_size * self.board_size
        if not (0 <= to < n):
            raise ValueError("move.to out of bounds")
        if to not in _neighbors(self.board_size, src):
            raise ValueError("move.to must be adjacent")

        occ_now = _occupied_positions(w)
        if to in occ_now:
            raise ValueError("move.to occupied")
        if b[to] >= 4:
            raise ValueError("move.to is a dome")
        if b[to] - b[src] > 1:
            raise ValueError("move climb too steep")

        # apply movement
        w2 = [[int(w[0][0]), int(w[0][1])], [int(w[1][0]), int(w[1][1])]]  # type: ignore[arg-type]
        w2[player][widx] = int(to)

        ply2 = int(s["ply"]) + 1

        # Winning move ends immediately; building is ignored/optional.
        if b[to] == 3:
            return {
                "phase": "play",
                "ply": ply2,
                "board": b,
                "workers": w2,
                "winner": int(player),
                "reason": "reach_level3",
            }

        if build is None:
            raise ValueError("non-winning moves must include build")

        if not (0 <= build < n):
            raise ValueError("build out of bounds")
        if build not in _neighbors(self.board_size, to):
            raise ValueError("build must be adjacent to destination")

        occ_after = _occupied_positions(w2)
        if build in occ_after:
            raise ValueError("build on occupied cell")
        if b[build] >= 4:
            raise ValueError("build on dome")

        b[build] += 1
        return {
            "phase": "play",
            "ply": ply2,
            "board": b,
            "workers": w2,
            "winner": None,
            "reason": "",
        }


def _as_state(state: JSONValue) -> dict[str, JSONValue]:
    if not isinstance(state, dict):
        raise ValueError(f"state must be dict, got: {type(state).__name__}")
    return state


def _as_board(board: JSONValue) -> list[int]:
    if not isinstance(board, list) or not all(isinstance(x, int) for x in board):
        raise ValueError("state.board must be list[int]")
    return [int(x) for x in board]


def _as_workers(workers: JSONValue) -> list[list[int | None]]:
    if not isinstance(workers, list) or len(workers) != 2:
        raise ValueError("state.workers must be [[..],[..]]")
    out: list[list[int | None]] = []
    for p in workers:
        if not isinstance(p, list) or len(p) != 2:
            raise ValueError("state.workers entries must be [pos,pos]")
        row: list[int | None] = []
        for v in p:
            if v is None:
                row.append(None)
            elif isinstance(v, int):
                row.append(int(v))
            else:
                raise ValueError("worker positions must be int or None")
        out.append(row)
    return out


def _occupied_positions(workers: list[list[int | None]]) -> set[int]:
    out: set[int] = set()
    for p in workers:
        for v in p:
            if isinstance(v, int):
                out.add(v)
    return out


def _neighbors(n: int, idx: int) -> list[int]:
    r, c = divmod(idx, n)
    out: list[int] = []
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            rr = r + dr
            cc = c + dc
            if 0 <= rr < n and 0 <= cc < n:
                out.append(rr * n + cc)
    return out


def _winner_on_turn_limit(s: dict[str, JSONValue]) -> PlayerId | None:
    b = _as_board(s["board"])
    w = _as_workers(s["workers"])

    def score(pid: int) -> tuple[int, int]:
        # primary: sum of worker heights; tie-break: highest single worker height
        heights = []
        for pos in w[pid]:
            if isinstance(pos, int):
                heights.append(b[pos])
            else:
                heights.append(0)
        return (sum(heights), max(heights))

    s0 = score(0)
    s1 = score(1)
    if s0 > s1:
        return 0
    if s1 > s0:
        return 1
    return None
