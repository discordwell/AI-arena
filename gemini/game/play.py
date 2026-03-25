import argparse
import sys
import os
import time
import random

# Ensure import paths
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../src')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from gemini.game.game import GeminiGame, Terminal
from gemini.game.gui import LaserGridGUI
from gemini.game.agent import RandomAgent, GeminiAgent

def main():
    parser = argparse.ArgumentParser(description="Play Laser Grid (Photon)")
    parser.add_argument("--mode", choices=["hh", "hm", "mm"], default="hm", help="Game mode: hh (Human vs Human), hm (Human vs Machine), mm (Machine vs Machine)")
    parser.add_argument("--ai", choices=["random", "gemini"], default="random", help="AI Agent type")
    
    args = parser.parse_args()
    
    game = GeminiGame()
    state = game.initial_state()
    gui = LaserGridGUI(game)
    
    # Setup Agents
    ai_agent_cls = RandomAgent
    if args.ai == "gemini":
        try:
            ai_agent_cls = GeminiAgent
        except Exception as e:
            print(f"Failed to initialize Gemini Agent: {e}. Falling back to Random.")
            ai_agent_cls = RandomAgent

    # Instantiate AI
    ai_instance = None
    if args.ai == "gemini":
         try:
            ai_instance = GeminiAgent()
         except Exception as e:
            print(f"Error creating Gemini Agent: {e}")
            ai_instance = RandomAgent()
    else:
        ai_instance = RandomAgent()

    agents = {}
    if args.mode == "hh":
        agents[0] = "human"
        agents[1] = "human"
    elif args.mode == "hm":
        agents[0] = "human"
        agents[1] = ai_instance
    elif args.mode == "mm":
        agents[0] = ai_instance
        # If MM, maybe need two instances? Or shared?
        # For simplicity, shared for Random, but Gemini might need state separation if it had memory (it doesn't here).
        agents[1] = ai_instance
        
    print(f"Starting Game: {args.mode} with AI: {args.ai}")
    
    gui.render(state)
    
    while True:
        # Check Terminal
        term = game.terminal(state)
        if term.is_terminal:
            print(f"Game Over! Winner: {term.winner} Reason: {term.reason}")
            gui.message = f"Game Over! Winner: {term.winner}"
            gui.render(state)
            pygame.time.wait(3000) # Show end state
            break
            
        player = state["turn_count"] % 2
        agent = agents[player]
        
        move = None
        if agent == "human":
            move = gui.get_human_move(state, player)
        else:
            # AI
            gui.message = f"AI ({args.ai}) thinking..."
            gui.render(state)
            
            # Additional small delay for visuals if random
            if args.ai == "random":
                time.sleep(0.5) 
            
            move = agent.get_move(game, state, player)
            
        if not move:
            print(f"Player {player} has no legal moves! Passing/Forfeiting.")
            # If no moves, maybe skip turn or lose? 
            # In Chess if no moves and not check -> StaleMate.
            # Here: If cannot move, is it loss? 
            # Rules: "The active player must perform exactly one action".
            # So if cannot move, they lose?
            # For now, let's just break to avoid infinite loop.
            # Or pass?
            # Actually, `legal_moves` should always have something unless trapped completely.
            break
            
        # Apply Move
        print(f"Player {player} executes: {move['type']} at {move['src']}")
        next_state = game.apply_move(state, player, move)
        
        # Animate / Show Result
        # The `apply_move` returns traces in `next_state` if lasers fired.
        gui.animate_lasers(next_state, next_state.get("traces", []))
        
        state = next_state
        gui.render(state)

if __name__ == "__main__":
    import pygame # Import here to ensure it's available
    main()
