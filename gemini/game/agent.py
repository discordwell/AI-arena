import random
import os
import json
import google.generativeai as genai
from typing import Optional

from gemini.game.game import GeminiGame
from gemini.game.board import Board, PieceType

class Agent:
    def get_move(self, game: GeminiGame, state: dict, player_id: int) -> Optional[dict]:
        raise NotImplementedError

class RandomAgent(Agent):
    def get_move(self, game: GeminiGame, state: dict, player_id: int) -> Optional[dict]:
        moves = game.legal_moves(state, player_id)
        if not moves:
            return None
        return random.choice(moves)

class GeminiAgent(Agent):
    def __init__(self, model_name="gemini-1.5-flash", api_key=None):
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError("GOOGLE_API_KEY environment variable not set.")
        
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel(model_name)
        
    def get_move(self, game: GeminiGame, state: dict, player_id: int) -> Optional[dict]:
        moves = game.legal_moves(state, player_id)
        if not moves:
            return None
            
        # Construct Prompt
        board = Board.from_json(state["board"])
        
        # ASCII Board Representation
        board_str = self.board_to_ascii(board)
        
        prompt = f"""
You are playing a turn-based strategy game called Laser Grid (Photon).
Your objective is to destroy the opponent's King while protecting your own.

**Rules:**
- Board: 10x10 Grid.
- Pieces:
  - King (K): 1 HP. Objective.
  - Shooter (S): Fires laser at end of turn. Rotates.
  - Mirror (M): Reflects lasers 90 degrees.
  - Block (B): Absorbs damage.
- Turn Logic:
  1. Move one piece OR Rotate one piece.
  2. ALL Shooters fire lasers.
  3. Lasers destroy pieces they hit (except Blocks take damage, Mirrors reflect).
  
**Current State:**
- You are Player {player_id} ({'Blue' if player_id == 1 else 'Red'}).
- Opponent is Player {1-player_id}.
- Turn: {state['turn_count']}

**Board:**
{board_str}

**Legal Moves:**
I have calculated {len(moves)} legal moves for you. 
Here is a sample of JSON moves you can make:
{json.dumps(moves[:20])} ... (showing first 20 of {len(moves)})

**Task:**
Analyze the board. Select the BEST move to either:
1. Hit an enemy piece (especially King).
2. Protect your King.
3. Improve your position.

Return ONLY a JSON object representing the move. Do not add markdown formatting.
Format: {{"type": "move", "src": [r, c], "dst": [r, c]}} OR {{"type": "rotate", "src": [r, c], "dir": 1}}
"""
        
        try:
            response = self.model.generate_content(prompt)
            # Cleanup response
            text = response.text.strip()
            if text.startswith("```json"):
                text = text[7:-3]
            elif text.startswith("```"):
                text = text[3:-3]
            
            move = json.loads(text)
            
            # Basic validation: Check if move is in legal_moves
            # Note: The model might return a valid move that is logically correct but syntax slightly off or not in exact list object equality.
            # We explicitly check values.
            
            # Normalize move to ensure int types
            if move["type"] == "move":
                move["src"] = [int(x) for x in move["src"]]
                move["dst"] = [int(x) for x in move["dst"]]
            elif move["type"] == "rotate":
                move["src"] = [int(x) for x in move["src"]]
                move["dir"] = int(move["dir"])
            
            # Verify legality
            if any(self.compare_moves(move, m) for m in moves):
                return move
            else:
                print(f"Gemini suggested illegal move: {move}. Fallback to Random.")
                return random.choice(moves)
                
        except Exception as e:
            print(f"Gemini Error: {e}. Fallback to Random.")
            return random.choice(moves)

    def compare_moves(self, m1, m2):
        if m1["type"] != m2["type"]: return False
        if m1["src"] != m2["src"]: return False
        if m1["type"] == "move":
            return m1["dst"] == m2["dst"]
        elif m1["type"] == "rotate":
            return m1["dir"] == m2["dir"]
        return False

    def board_to_ascii(self, board):
        lines = ["   0 1 2 3 4 5 6 7 8 9"]
        for r in range(board.rows):
            line = [f"{r} "]
            for c in range(board.cols):
                p = board.get(r, c)
                if not p:
                    line.append(".")
                else:
                    # Generic symbol + Player ID
                    # e.g. S0, K1, M0
                    sym = p.type.value[0] # K, S, M, B
                    pid = str(p.player)
                    # Add orientation for context
                    ori = ""
                    if p.type == PieceType.SHOOTER:
                        dirs = ["^", ">", "v", "<"]
                        ori = dirs[p.orientation]
                    elif p.type == PieceType.MIRROR:
                        ori = "/" if p.orientation % 2 == 0 else "\\"
                    
                    line.append(f"{sym}{pid}{ori}")
            lines.append(" ".join(line))
        return "\n".join(lines)
