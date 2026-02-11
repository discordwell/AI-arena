from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, List, Tuple, Dict, Set

from ai_arena.json_types import JSONValue

# Directions
# 0: N, 1: E, 2: S, 3: W
DIRS = [(-1, 0), (0, 1), (1, 0), (0, -1)]

class PieceType(str, Enum):
    KING = "K"
    SHOOTER = "S"
    MIRROR = "M"
    BLOCK = "B"

@dataclass(slots=True)
class Piece:
    type: PieceType
    player: int
    hp: int
    # Orientation 0-3. 
    # For Shooter: 0=N, 1=E...
    # For Mirror: 0 = /, 1 = \, 2 = /, 3 = \ (alternating for simplicity in rotation logic)
    # / reflects: N<->E (0<->1), S<->W (2<->3)
    # \ reflects: N<->W (0<->3), S<->E (2<->1)
    orientation: int = 0

    def to_json(self) -> JSONValue:
        return {
            "type": self.type.value,
            "player": self.player,
            "hp": self.hp,
            "orientation": self.orientation
        }

    @staticmethod
    def from_json(data: JSONValue) -> Piece:
        return Piece(
            type=PieceType(data["type"]),
            player=data["player"],
            hp=data["hp"],
            orientation=data["orientation"]
        )

    def rotate(self, direction: int):
        # direction: 1 for CW, -1 for CCW
        self.orientation = (self.orientation + direction) % 4
    
    def symbol(self) -> str:
        if self.type == PieceType.KING:
            return "♔" if self.player == 0 else "♚"
        elif self.type == PieceType.SHOOTER:
             arrows = ["↑", "→", "↓", "←"]
             return arrows[self.orientation]
        elif self.type == PieceType.MIRROR:
            return "/" if self.orientation % 2 == 0 else "\\"
        elif self.type == PieceType.BLOCK:
            return "■"
        return "?"

@dataclass
class LaserHit:
    pos: Tuple[int, int]
    damage: int = 0
    stop: bool = False

@dataclass
class Board:
    rows: int
    cols: int
    grid: List[List[Optional[Piece]]] = field(default_factory=list)

    @classmethod
    def empty(cls, rows=10, cols=10) -> Board:
        grid = [[None for _ in range(cols)] for _ in range(rows)]
        return cls(rows=rows, cols=cols, grid=grid)

    def to_json(self) -> JSONValue:
        return [
            [p.to_json() if p else None for p in row]
            for row in self.grid
        ]

    @staticmethod
    def from_json(data: JSONValue) -> Board:
        rows = len(data)
        cols = len(data[0]) if rows > 0 else 0
        grid = [
            [Piece.from_json(p) if p else None for p in row]
            for row in data
        ]
        return Board(rows=rows, cols=cols, grid=grid)

    def in_bounds(self, r: int, c: int) -> bool:
        return 0 <= r < self.rows and 0 <= c < self.cols

    def get(self, r: int, c: int) -> Optional[Piece]:
        if not self.in_bounds(r, c):
            return None
        return self.grid[r][c]

    def set(self, r: int, c: int, p: Optional[Piece]):
        if self.in_bounds(r, c):
            self.grid[r][c] = p

    def move_piece(self, r1: int, c1: int, r2: int, c2: int) -> bool:
        if not self.in_bounds(r2, c2) or self.get(r2, c2) is not None:
            return False
        p = self.get(r1, c1)
        if p is None:
            return False
        self.set(r2, c2, p)
        self.set(r1, c1, None)
        return True

    def fire_lasers(self) -> List[dict]:
        # Returns list of trace events for animation: {"path": [[r,c], ...], "hit": [r,c]}
        # Also applies damage
        
        # 1. Identify shooters
        beams = []
        for r in range(self.rows):
            for c in range(self.cols):
                p = self.get(r, c)
                if p and p.type == PieceType.SHOOTER:
                    # Initial beam
                    dr, dc = DIRS[p.orientation]
                    beams.append({
                        "r": r + dr, "c": c + dc, 
                        "dir": p.orientation, 
                        "path": [(r,c)],
                        "owner": p.player
                    })

        # 2. Simulate step by step (to handle head-on collisions effectively we might need a finer simulation, 
        # but for turn-based simple logic, we can trace each beam until it hits or exits)
        # NOTE: Rules say "Simultaneous". Head-on collision needs care.
        # Simplification: Trace all fully. If two paths invoke opposite directions on same edge -> collision.
        # Use a simpler "instant travel" model first. Head-on collision is edge case.
        
        # We will apply damage after calculation to avoid order-dependency in death.
        damage_map: Dict[Tuple[int, int], int] = {}
        traces = []

        for beam in beams:
            curr_r, curr_c = beam["r"], beam["c"]
            curr_dir = beam["dir"]
            path = beam["path"]
            
            while self.in_bounds(curr_r, curr_c):
                path.append((curr_r, curr_c))
                target = self.get(curr_r, curr_c)
                
                if target:
                    # Hit something
                    if target.type == PieceType.MIRROR:
                        # Reflected?
                        # 0(/): N(0)->E(1), E(1)->N(0), S(2)->W(3), W(3)->S(2)
                        # 1(\): N(0)->W(3), E(1)->S(2), S(2)->E(1), W(3)->N(0)
                        m_type = target.orientation % 2 # 0 or 1
                        refl_dir = -1
                        
                        # Incoming direction is opposite of beam motion for "hitting face" check
                        # But simpler: match curr_dir -> new_dir
                        
                        if m_type == 0: # /
                            if curr_dir == 0: refl_dir = 1 # N->E
                            elif curr_dir == 1: refl_dir = 0 # E->N
                            elif curr_dir == 2: refl_dir = 3 # S->W
                            elif curr_dir == 3: refl_dir = 2 # W->S
                        else: # \
                            if curr_dir == 0: refl_dir = 3 # N->W
                            elif curr_dir == 1: refl_dir = 2 # E->S
                            elif curr_dir == 2: refl_dir = 1 # S->E
                            elif curr_dir == 3: refl_dir = 0 # W->N
                            
                        # Check if reflection is valid (hitting the mirror side)
                        # Actually rules said "1-sided" in plan but "2-sided" in simplification.
                        # "Simplification for v1: Mirrors reflect from TWO sides"
                        # So we always reflect.
                        
                        curr_dir = refl_dir
                        dr, dc = DIRS[curr_dir]
                        curr_r += dr
                        curr_c += dc
                        continue # Continue trace
                        
                    elif target.type == PieceType.BLOCK:
                        # Absorb
                        damage_map[(curr_r, curr_c)] = damage_map.get((curr_r, curr_c), 0) + 1
                        break
                    
                    elif target.type in [PieceType.KING, PieceType.SHOOTER]:
                        # Die
                        damage_map[(curr_r, curr_c)] = damage_map.get((curr_r, curr_c), 0) + 1
                        break
                        
                    # Should not overlap pieces generally, but if so..
                    else:
                        break # Stop at any other obstacle
                
                # Move forward
                dr, dc = DIRS[curr_dir]
                curr_r += dr
                curr_c += dc
            
            traces.append({
                "path": path,
                "owner": beam["owner"]
            })

        # Apply damage
        for (r, c), dmg in damage_map.items():
            p = self.get(r, c)
            if p:
                p.hp -= dmg
                if p.hp <= 0:
                    self.set(r, c, None) # Destroy
        
        return traces
