import pytest
import sys
import os

# Ensure we can import from src and project root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../src'))) # for ai_arena
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))) # for gemini

from gemini.game.game import GeminiGame
from gemini.game.board import Board, Piece, PieceType

def test_initialization():
    game = GeminiGame()
    state = game.initial_state()
    board = Board.from_json(state["board"])
    
    # Check Kings
    k0 = board.get(0, 4)
    assert k0.type == PieceType.KING and k0.player == 0
    k1 = board.get(9, 5)
    assert k1.type == PieceType.KING and k1.player == 1
    
    # Check Shooters
    s0 = board.get(1, 2)
    assert s0.type == PieceType.SHOOTER and s0.player == 0 and s0.orientation == 2 # S

def test_movement():
    game = GeminiGame()
    # Use empty board to avoid getting shot by default setup
    board = Board.empty()
    board.set(1, 2, Piece(PieceType.SHOOTER, 0, 1, 0)) # Facing N
    
    state = {"board": board.to_json(), "turn_count": 0}
    
    # Player 0 move shooter at (1, 2) to (2, 2)
    move = {"type": "move", "src": [1, 2], "dst": [2, 2]}
    next_state = game.apply_move(state, 0, move)
    
    board = Board.from_json(next_state["board"])
    assert board.get(1, 2) is None
    s = board.get(2, 2)
    assert s is not None and s.type == PieceType.SHOOTER

def test_rotation():
    game = GeminiGame()
    # Use empty board
    board = Board.empty()
    # Shooter at (1, 2) facing S(2)
    board.set(1, 2, Piece(PieceType.SHOOTER, 0, 1, 2))
    
    state = {"board": board.to_json(), "turn_count": 0}
    
    # Player 0 rotates shooter at (1, 2) CW (from S(2) to W(3))
    move = {"type": "rotate", "src": [1, 2], "dir": 1}
    next_state = game.apply_move(state, 0, move)
    
    board = Board.from_json(next_state["board"])
    s = board.get(1, 2)
    # 2 + 1 = 3 (W)
    assert s.orientation == 3

def test_laser_kill():
    # Construct a scenario where shooter kills opponent
    board = Board.empty()
    # Shooter P0 at (0,0) facing East (1)
    # Target P1 King at (0, 5)
    board.set(0, 0, Piece(PieceType.SHOOTER, 0, 1, 1))
    board.set(0, 5, Piece(PieceType.KING, 1, 1, 0))
    
    state = {"board": board.to_json(), "turn_count": 0}
    game = GeminiGame()
    
    # P0 does a null move (just rotates in place to trigger fire)
    # Actually, let's just apply a dummy move that doesn't affect the shooter's aim
    move = {"type": "rotate", "src": [0, 0], "dir": 0} 
    # Rotate 0 means we do nothing? No. Dir must be 1/-1.
    # If we rotate 1 (CW), N->E. S->W. E->S.
    # Wait, 1(E) + 1 = 2(S).
    # So if we rotate, we MISS.
    # We need to set it up so it faces target AFTER rotation.
    # Target is East (0, 5). We need to face East (1).
    # Start facing North (0). Rotate CW (1) -> East (1).
    
    board.set(0, 0, Piece(PieceType.SHOOTER, 0, 1, 0)) # Facing N
    state = {"board": board.to_json(), "turn_count": 0}
    
    move = {"type": "rotate", "src": [0, 0], "dir": 1} # N->E
    next_state = game.apply_move(state, 0, move)
    
    board_next = Board.from_json(next_state["board"])
    # King should be dead
    p_king = board_next.get(0, 5)
    assert p_king is None or p_king.hp <= 0
    
    # Check traces
    assert "traces" in next_state
    trace = next_state["traces"][0]
    # Path should go from (0,0) to (0,5)
    path_coords = trace["path"]
    assert (0, 5) in path_coords

def test_laser_reflection():
    board = Board.empty()
    # Shooter P0 at (2, 0) facing East (1)
    # Mirror at (2, 5) type / (orientation=0 or 2)
    # / reflects E(1) -> N(0)
    # Target Block at (0, 5)
    
    board.set(2, 0, Piece(PieceType.SHOOTER, 0, 1, 1)) # Facing E
    board.set(2, 5, Piece(PieceType.MIRROR, 1, 1, 0)) # /
    board.set(0, 5, Piece(PieceType.BLOCK, 1, 2, 0)) # HP 2
    
    state = {"board": board.to_json(), "turn_count": 0}
    game = GeminiGame()
    
    # Dummy move (rotate shooter no-op?)
    # Just rotate it 360? Or rotate dummy piece.
    board.set(9, 9, Piece(PieceType.SHOOTER, 0, 1, 0))
    move = {"type": "rotate", "src": [9, 9], "dir": 1}
    state["board"] = board.to_json()
    
    next_state = game.apply_move(state, 0, move)
    
    board_next = Board.from_json(next_state["board"])
    
    # Block at (0,5) should take damage. HP 2->1
    blk = board_next.get(0, 5)
    assert blk is not None, "Block was destroyed (HP <= 0)"
    assert blk.hp == 1, f"Block HP is {blk.hp}, expected 1"
    
    # Trace check
    traces = next_state["traces"]
    # Identify the trace from (2,0)
    relevant_trace = None
    for t in traces:
        # Check if t starts roughly at (2,0). 
        # t["path"][0] is (2,0).
        if t["path"][0] == (2, 0):
            relevant_trace = t
            break
            
    assert relevant_trace
    # Contains reflection point (2,5) and hit point (0,5)
    assert (2, 5) in relevant_trace["path"]
    assert (0, 5) in relevant_trace["path"]
