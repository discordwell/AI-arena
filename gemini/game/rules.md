# Photon - Game Rules

**Photon** is a 2-player turn-based tactical game played on a 10x10 grid. The objective is to eliminate the opponent's **King** using laser beams.

## Components

### Board
*   10 columns (x=0..9) x 10 rows (y=0..9). 
*   (0,0) is Top-Left.

### Pieces
Each player controls a set of pieces.
*   **King (K)**: 1 per player. **Objective**: Destroy enemy King. 1 HP. Cannot rotate. Moves 1 square.
*   **Shooter (S)**: Fires a laser beam at the end of *every* turn. 1 HP. Rotates. Moves 1 square.
*   **Mirror (M)**: Reflects lasers 90 degrees. Has a specific orientation (/, \). 1 HP.
    *   **Reflective Side**: If a laser hits the "flat" side (e.g. `/` from Top or Right), it reflects.
    *   **Non-Reflective Side**: If a laser hits the "back" or "side" (e.g. `/` from Bottom or Left), the Mirror is destroyed.
    *   *Note*: A specific orientation logic will be enforced in the engine (e.g. `/` is "NE-SW", `\` is "NW-SE").
*   **Block (B)**: Absorbs laser fire. Has 2 HP. Reduces by 1 on hit. Destroyed at 0 HP.

## Turn Structure
The game is played in turns. Player 0 (Red) goes first, then Player 1 (Blue).

### 1. Action Phase
The active player must perform **exactly one** action:
*   **Move**: Move a piece to an adjacent empty square (Orthogonal only: Up, Down, Left, Right).
*   **Rotate**: Rotate a piece 90 degrees clockwise or counter-clockwise.

### 2. Laser Phase
After the move, **ALL** Shooters (both Red and Blue) fire a laser beam simultaneously.
*   Lasers travel in the direction the Shooter is facing (N, E, S, W).
*   Lasers travel instantly until they hit a Piece or the Edge of the board.
*   **Hit Resolution**:
    *   **Mirror (Reflective)**: Beam changes direction 90° and continues.
    *   **Block**: Damage -1 HP. Laser stops.
    *   **King/Shooter/Mirror (Non-Reflective)**: Destroyed immediately (HP->0). Laser stops.
    *   **Head-on Collision**: If two lasers meet head-on, or intersect at the same square at the same step, they annihilate each other at that point.

### 3. Cleanup
*   Pieces with 0 HP are removed from the board.
*   If a King has 0 HP, the owner loses. If both Kings die same turn -> Draw (or Player 0 wins? No, Draw).

## Technical API

State is a dictionary:
```json
{
  "board": [[Piece or null, ...], ...],
  "turn": int,
  "history": [...]
}
```

Moves are JSON:
```json
{"type": "move", "src": [x, y], "dst": [x, y]}
{"type": "rotate", "src": [x, y], "dir": 1}  // 1 = CW, -1 = CCW
```
