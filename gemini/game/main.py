from __future__ import annotations

import random
import time
import os
import sys

# Ensure import paths
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../src')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from gemini.game.game import GeminiGame

def main():
    game = GeminiGame()
    state = game.initial_state()
    
    print(f"Starting {game.name}...")
    print(game.render(state))
    print("-" * 20)
    
    while True:
        term = game.terminal(state)
        if term.is_terminal:
            print(f"Game Over! Winner: {term.winner} Reason: {term.reason}")
            break
            
        # Determine current player based on previous moves?
        # ai_arena.Game protocol doesn't explicitly track turn order on API, usually passed by engine.
        # But here my state has "turn_count". Rules say P0 then P1.
        player = state["turn_count"] % 2
        
        moves = game.legal_moves(state, player)
        if not moves:
            print(f"Player {player} has no legal moves! Skipping/Forfeiting?")
            break
            
        # AI Logic: Random
        move = random.choice(moves)
        
        # Apply
        print(f"Player {player} executes: {move['type']} at {move['src']}")
        state = game.apply_move(state, player, move)
        
        # Render
        print("\033[H\033[J") # Clear screen (optional, maybe just print)
        # Actually better to just print so we can scroll back in logs
        print(game.render(state))
        print("-" * 20)
        
        # Wait a bit if watching
        # time.sleep(0.5) 
        # For automated run, don't sleep.

if __name__ == "__main__":
    main()
