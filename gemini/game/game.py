from __future__ import annotations

import copy
from dataclasses import dataclass

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))

from ai_arena.game import Game, PlayerId, Terminal
from ai_arena.json_types import JSONValue
from board import Board, Piece, PieceType, DIRS

# ANSI Colors
RED = "\033[91m"
BLUE = "\033[94m"
RESET = "\033[0m"
# Backgrounds
BG_RED = "\033[41m"
BG_BLUE = "\033[44m"
BG_BLACK = "\033[40m"

@dataclass(slots=True)
class GeminiGame:
    name: str = "photon_laser_tactics"

    def initial_state(self) -> JSONValue:
        board = Board.empty()
        
        # Setup initial pieces for a standard game
        # Player 0 (Red) at Top (Rows 0-1)
        # Player 1 (Blue) at Bottom (Rows 8-9)
        
        # Kings
        board.set(0, 4, Piece(PieceType.KING, 0, 1, 0))
        board.set(9, 5, Piece(PieceType.KING, 1, 1, 0)) # Oppostite side
        
        # Shooters
        board.set(1, 2, Piece(PieceType.SHOOTER, 0, 1, 2)) # Facing S
        board.set(1, 7, Piece(PieceType.SHOOTER, 0, 1, 2))
        
        board.set(8, 2, Piece(PieceType.SHOOTER, 1, 1, 0)) # Facing N
        board.set(8, 7, Piece(PieceType.SHOOTER, 1, 1, 0))
        
        # Mirrors
        # Simple defensive line
        board.set(2, 3, Piece(PieceType.MIRROR, 0, 1, 1)) # \
        board.set(2, 6, Piece(PieceType.MIRROR, 0, 1, 0)) # /
        
        board.set(7, 3, Piece(PieceType.MIRROR, 1, 1, 0)) # /
        board.set(7, 6, Piece(PieceType.MIRROR, 1, 1, 1)) # \
        
        # Blocks (Shields)
        board.set(1, 4, Piece(PieceType.BLOCK, 0, 2, 0))
        board.set(8, 5, Piece(PieceType.BLOCK, 1, 2, 0))

        return {
            "board": board.to_json(),
            "turn_count": 0
        }

    def legal_moves(self, state: JSONValue, player: PlayerId) -> list[JSONValue]:
        board = Board.from_json(state["board"])
        moves = []
        
        for r in range(board.rows):
            for c in range(board.cols):
                p = board.get(r, c)
                if p and p.player == player:
                    # 1. Move Actions
                    for i, (dr, dc) in enumerate(DIRS):
                        nr, nc = r + dr, c + dc
                        if board.move_piece(r, c, nr, nc): # Check if valid (bounds + empty)
                             # Revert is needed if we actually modified, but move_piece does modify.
                             # But here we just want to know if valid.
                             # move_piece checks basic validity.
                             # Actually `board.move_piece` modifies the board. 
                             # We should check `in_bounds` and `get` manually or clone.
                             # Optimization: Manual check is faster.
                            pass
                        
                        if board.in_bounds(nr, nc) and board.get(nr, nc) is None:
                             moves.append({
                                 "type": "move",
                                 "src": [r, c],
                                 "dst": [nr, nc]
                             })
                    
                    # 2. Rotate Actions (Only for Shooter and Mirror)
                    if p.type in [PieceType.SHOOTER, PieceType.MIRROR]:
                        moves.append({"type": "rotate", "src": [r, c], "dir": 1}) # CW
                        moves.append({"type": "rotate", "src": [r, c], "dir": -1}) # CCW
                        
        return moves

    def apply_move(self, state: JSONValue, player: PlayerId, move: JSONValue) -> JSONValue:
        board = Board.from_json(state["board"])
        
        # Execute Action
        if move["type"] == "move":
            src = move["src"]
            dst = move["dst"]
            board.move_piece(src[0], src[1], dst[0], dst[1])
        elif move["type"] == "rotate":
            src = move["src"]
            direction = move["dir"]
            p = board.get(src[0], src[1])
            if p:
                p.rotate(direction)
        
        # Fire Lasers
        traces = board.fire_lasers()
        
        return {
            "board": board.to_json(),
            "turn_count": state["turn_count"] + 1,
            "traces": traces
        }

    def terminal(self, state: JSONValue) -> Terminal:
        board = Board.from_json(state["board"])
        
        kings = {0: False, 1: False}
        for r in range(board.rows):
            for c in range(board.cols):
                p = board.get(r, c)
                if p and p.type == PieceType.KING:
                    kings[p.player] = True
        
        if not kings[0] and not kings[1]:
            return Terminal(is_terminal=True, winner=None, reason="Draw (Double KO)")
        if kings[0] and not kings[1]: # Player 1 lost
             return Terminal(is_terminal=True, winner=0, reason="Player 1 King eliminated")
        if kings[1] and not kings[0]: # Player 0 lost
             return Terminal(is_terminal=True, winner=1, reason="Player 0 King eliminated")
        # Fix logic: "kings" stores ALIVE status. 
        # If kings[0] is True, Player 0 is ALIVE.
        
        if state["turn_count"] > 30: # Max turns (capped for tournament speed)
             return Terminal(is_terminal=True, winner=None, reason="Max turns reached")

        return Terminal(is_terminal=False, winner=None, reason="")

    def render(self, state: JSONValue) -> str:
        board = Board.from_json(state["board"])
        lines = []
        lines.append(f"Turn: {state['turn_count']}")
        
        # Grid for laser overlay
        laser_map = {}
        if "traces" in state:
            for trace in state["traces"]:
                color = RED if trace["owner"] == 0 else BLUE
                path = trace["path"]
                for r, c in path:
                    laser_map[(r, c)] = color + "*" + RESET
        
        # Border
        lines.append("  " + " ".join(str(i) for i in range(board.cols)))
        
        for r in range(board.rows):
            line_parts = [f"{r} "]
            for c in range(board.cols):
                p = board.get(r, c)
                cell_str = "."
                
                # Laser overlay priority: if no piece, show laser
                if (r, c) in laser_map and not p:
                    cell_str = laser_map[(r, c)]
                
                if p:
                    color = RED if p.player == 0 else BLUE
                    sym = p.symbol()
                    # If piece enters laser path (it was hit), show it "GLOWING"?
                    # For now just show piece.
                    cell_str = color + sym + RESET
                
                line_parts.append(cell_str)
            lines.append(" ".join(line_parts))
            
        return "\n".join(lines)
