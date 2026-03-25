import pygame
import sys
import copy
from typing import Optional, Tuple, List, Dict

from gemini.game.board import Piece, PieceType, Board

# Constants
CELL_SIZE = 60
GRID_SIZE = 10
WIDTH = CELL_SIZE * GRID_SIZE
HEIGHT = CELL_SIZE * GRID_SIZE + 100  # Extra space for UI/Info
FPS = 30

# Colors
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GRAY = (200, 200, 200)
DARK_GRAY = (50, 50, 50)
RED = (200, 50, 50)
BLUE = (50, 50, 200)
GREEN = (50, 200, 50)
YELLOW = (255, 255, 0)
CYAN = (0, 255, 255)
LASER_RED = (255, 0, 0)
LASER_BLUE = (0, 0, 255) 

class LaserGridGUI:
    def __init__(self, game):
        pygame.init()
        self.game = game
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        pygame.display.set_caption(f"Laser Grid - {game.name}")
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("Arial", 24)
        self.small_font = pygame.font.SysFont("Arial", 16)
        
        self.selected_pos: Optional[Tuple[int, int]] = None
        self.valid_moves: List[dict] = []
        self.animation_traces = []
        self.message = ""

    def handle_input(self, state, player_id: int) -> Optional[dict]:
        """
        Handle user input for the current player.
        Returns a move dict if a move is confirmed, else None.
        """
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit()
            
            if event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1: # Left Click
                    x, y = event.pos
                    if y < HEIGHT - 100: # Within board area
                        c = x // CELL_SIZE
                        r = y // CELL_SIZE
                        self.on_click(state, player_id, r, c)
            
            if event.type == pygame.KEYDOWN:
                if self.selected_pos:
                    r, c = self.selected_pos
                    # Rotate keys
                    if event.key == pygame.K_q: # Rotate CCW
                        return self.create_rotate_move(r, c, -1)
                    elif event.key == pygame.K_e: # Rotate CW
                        return self.create_rotate_move(r, c, 1)
                    
                    # Move keys (Arrow keys for movement if selected)
                    # Note: We handle move by verify click on destination, 
                    # but keyboard can be added for adjacent moves.
                    dr, dc = 0, 0
                    if event.key == pygame.K_UP or event.key == pygame.K_w: dr = -1
                    elif event.key == pygame.K_DOWN or event.key == pygame.K_s: dr = 1
                    elif event.key == pygame.K_LEFT or event.key == pygame.K_a: dc = -1
                    elif event.key == pygame.K_RIGHT or event.key == pygame.K_d: dc = 1
                    
                    if dr != 0 or dc != 0:
                        nr, nc = r + dr, c + dc
                        # validate
                        for m in self.valid_moves:
                            if m["type"] == "move" and m["src"] == [r, c] and m["dst"] == [nr, nc]:
                                return m
                                
        return None

    def on_click(self, state, player_id, r, c):
        board = Board.from_json(state["board"])
        p = board.get(r, c)
        
        # If clicking a valid destination for selected piece
        if self.selected_pos:
            sr, sc = self.selected_pos
            # check if click is a valid move destination
            for m in self.valid_moves:
                if m["type"] == "move" and m["src"] == [sr, sc] and m["dst"] == [r, c]:
                    # We can't return from here easily, so we set a flag or rely on loop to pick it up?
                    # Ideally handle_input returns the move. 
                    # But on_click is helper. 
                    # Let's simple check: if valid move, we need to return it.
                    # But handle_input calls this. 
                    # We will store "pending_move" or similar?
                    # Actually, better: handle_input logic should be self-contained or this returns move.
                    pass
            
            if self.selected_pos == (r, c):
                # Deselect
                self.selected_pos = None
                self.valid_moves = []
                return

        # Select new piece
        if p and p.player == player_id:
            self.selected_pos = (r, c)
            self.valid_moves = self.game.legal_moves(state, player_id)
            # Filter moves for this piece only? legal_moves returns all. 
            self.valid_moves = [m for m in self.valid_moves if m["src"] == [r, c]]
            self.message = f"Selected {p.type.name} at {r},{c}"
        
        # If we clicked a destination
        elif self.selected_pos:
             # This part needs to be handled in handle_input to return the move.
             pass

    def get_human_move(self, state, player_id: int) -> dict:
        self.message = f"Player {player_id}'s Turn"
        self.selected_pos = None
        self.valid_moves = []
        
        while True:
            # Event loop within get_move to block until move
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                
                if event.type == pygame.MOUSEBUTTONDOWN:
                    if event.button == 1:
                         x, y = event.pos
                         if y < HEIGHT - 100:
                            c = x // CELL_SIZE
                            r = y // CELL_SIZE
                            
                            # Check if valid destination for current selection
                            if self.selected_pos:
                                sr, sc = self.selected_pos
                                for m in self.valid_moves:
                                    if m["type"] == "move" and m["src"] == [sr, sc] and m["dst"] == [r, c]:
                                        return m
                            
                            # Else try to select
                            self.on_click(state, player_id, r, c)
                
                if event.type == pygame.KEYDOWN:
                    if self.selected_pos:
                        r, c = self.selected_pos
                        move = None
                        if event.key == pygame.K_q: move = self.create_rotate_move(r, c, -1)
                        elif event.key == pygame.K_e: move = self.create_rotate_move(r, c, 1)
                        # Arrow keys
                        dr, dc = 0, 0
                        if event.key == pygame.K_UP or event.key == pygame.K_w: dr = -1
                        elif event.key == pygame.K_DOWN or event.key == pygame.K_s: dr = 1
                        elif event.key == pygame.K_LEFT or event.key == pygame.K_a: dc = -1
                        elif event.key == pygame.K_RIGHT or event.key == pygame.K_d: dc = 1
                        
                        if dr or dc:
                            nr, nc = r+dr, c+dc
                            for m in self.valid_moves:
                                if m["type"] == "move" and m["src"] == [r, c] and m["dst"] == [nr, nc]:
                                    move = m
                                    break
                                    
                        if move:
                            # Verify if allowed
                            # valid_moves already filtered for selected piece in on_click
                            # But we need to ensure it's in the list
                            if move in self.valid_moves or any(m == move for m in self.valid_moves): ## dict comparison works
                                return move

            self.render(state)
            self.clock.tick(FPS)

    def create_rotate_move(self, r, c, direction):
        return {"type": "rotate", "src": [r, c], "dir": direction}

    def render(self, state, laser_traces=None):
        self.screen.fill(BLACK)
        board = Board.from_json(state["board"])
        
        # Draw Grid
        for r in range(GRID_SIZE):
            for c in range(GRID_SIZE):
                rect = pygame.Rect(c * CELL_SIZE, r * CELL_SIZE, CELL_SIZE, CELL_SIZE)
                pygame.draw.rect(self.screen, DARK_GRAY, rect, 1)
                
                # Highlight selected
                if self.selected_pos == (r, c):
                    pygame.draw.rect(self.screen, YELLOW, rect, 3)
                
                # Highlight valid moves
                if self.selected_pos:
                    for m in self.valid_moves:
                        if m["type"] == "move" and m["dst"] == [r, c]:
                            s = pygame.Surface((CELL_SIZE, CELL_SIZE))
                            s.set_alpha(50)
                            s.fill(GREEN)
                            self.screen.blit(s, (c*CELL_SIZE, r*CELL_SIZE))

        # Draw Pieces
        for r in range(GRID_SIZE):
            for c in range(GRID_SIZE):
                p = board.get(r, c)
                if p:
                    self.draw_piece(p, r, c)

        # Draw Lasers
        if laser_traces:
            self.draw_lasers(laser_traces)

        # UI Area
        ui_rect = pygame.Rect(0, HEIGHT - 100, WIDTH, 100)
        pygame.draw.rect(self.screen, (30, 30, 30), ui_rect)
        
        # Info Text
        turn_text = self.font.render(f"Turn: {state['turn_count']} | {self.message}", True, WHITE)
        self.screen.blit(turn_text, (20, HEIGHT - 80))
        
        controls = "Controls: Select Piece + (Arrows to Move) or (Q/E to Rotate)"
        ctrl_text = self.small_font.render(controls, True, GRAY)
        self.screen.blit(ctrl_text, (20, HEIGHT - 40))

        pygame.display.flip()

    def draw_piece(self, piece: Piece, r, c):
        center_x = c * CELL_SIZE + CELL_SIZE // 2
        center_y = r * CELL_SIZE + CELL_SIZE // 2
        color = RED if piece.player == 0 else BLUE
        
        # Base shape
        if piece.type == PieceType.SHOOTER:
            pygame.draw.circle(self.screen, color, (center_x, center_y), CELL_SIZE // 3)
            # Direction indicator
            end_pos = self.get_direction_offset(center_x, center_y, piece.orientation, length=CELL_SIZE//2.5)
            pygame.draw.line(self.screen, WHITE, (center_x, center_y), end_pos, 3)
            
        elif piece.type == PieceType.MIRROR:
            # Draw triangle or line
            # shape / or \
            start_pos, end_pos = self.get_mirror_coords(c, r, piece.orientation)
            pygame.draw.line(self.screen, color, start_pos, end_pos, 5)
            # Draw a "back" to indicate non-reflective side? 
            # Or just a thick line.
            
        elif piece.type == PieceType.BLOCK:
            rect = pygame.Rect(c*CELL_SIZE + 10, r*CELL_SIZE + 10, CELL_SIZE - 20, CELL_SIZE - 20)
            pygame.draw.rect(self.screen, color, rect)
            # HP Text
            hp_text = self.small_font.render(str(piece.hp), True, BLACK)
            self.screen.blit(hp_text, (center_x - 5, center_y - 10))
            
        elif piece.type == PieceType.KING:
            # Star / Crown
            # Simple Cross for now
            pygame.draw.line(self.screen, color, (center_x - 15, center_y), (center_x + 15, center_y), 5)
            pygame.draw.line(self.screen, color, (center_x, center_y - 15), (center_x, center_y + 15), 5)
            # Border
            pygame.draw.circle(self.screen, WHITE, (center_x, center_y), CELL_SIZE // 3, 2)

    def get_direction_offset(self, cx, cy, orientation, length):
        # 0: N, 1: E, 2: S, 3: W
        if orientation == 0: return (cx, cy - length)
        if orientation == 1: return (cx + length, cy)
        if orientation == 2: return (cx, cy + length)
        if orientation == 3: return (cx - length, cy)
        return (cx, cy)

    def get_mirror_coords(self, c, r, orientation):
        # 0(/): BL to TR
        # 1(\): TL to BR
        pad = 10
        x1, y1 = c * CELL_SIZE, r * CELL_SIZE
        x2, y2 = (c + 1) * CELL_SIZE, (r + 1) * CELL_SIZE
        
        if orientation % 2 == 0: # /
            return ((x1 + pad, y2 - pad), (x2 - pad, y1 + pad))
        else: # \
            return ((x1 + pad, y1 + pad), (x2 - pad, y2 - pad))

    def draw_lasers(self, traces):
        for trace in traces:
            color = LASER_RED if trace["owner"] == 0 else LASER_BLUE
            path = trace["path"]
            if len(path) < 2: continue
            
            points = [(c * CELL_SIZE + CELL_SIZE // 2, r * CELL_SIZE + CELL_SIZE // 2) for r, c in path]
            pygame.draw.lines(self.screen, color, False, points, 3)

    def animate_lasers(self, state, traces):
        # Simple animation loop showing lasers for a moment
        # Or progressive drawing?
        # For now, just show them static for a second
        start = pygame.time.get_ticks()
        while pygame.time.get_ticks() - start < 1000: # 1 second
            self.render(state, traces)
            pygame.event.pump()
            self.clock.tick(FPS)
